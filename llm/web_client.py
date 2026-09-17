"""网页版 DeepSeek 客户端 —— Playwright 驱动的**全自动**调用。

设计参考 Video2Team 的 chat.deepseek.com 注入方案（见其 src/content/deepseek.ts，
本项目文档 PROJECT_DESIGN.md 6.6 节有对照说明），关键差异：

    Video2Team：半自动 —— 程序填好输入框，**由用户按一次回车**发送
    本项目：全自动 —— 程序完成 开页面 → 注入提示词/附件 → 按 Enter 发送
            → 嗅探新回复 → 稳定判定 → 读取全文 → 解析 JSON，全程无人值守

五项关键技术（与参考实现一一对应）：
  1. 浏览器与登录态：launch_persistent_context(user_data_dir) —— 扫码登录一次，
     登录态持久化，之后全自动复用
  2. React 受控组件：Playwright fill（内部走原生 setter）优先，回读校验，
     不一致时回退手写「原生 setter + input 事件」（等价 setNativeValue）
  3. 附件（可选图片）：set_input_files 到 input[type=file]（等价 DataTransfer
     构造 FileList 模拟真实选择）
  4. 回复检测：基线嗅探（.ds-markdown 节点数 + 末尾文本快照）→ 出现新回复
     （节点增加 或 文本变化）→ 文本连续稳定 1.5s 判定完成（双条件，
     兼容同会话追加回复与流式中途截断）
  5. 浏览器常驻：进程内单例 + 互斥锁，跨多次调用复用同一浏览器，避免反复
     启动与重复登录
"""
from __future__ import annotations

import atexit
import json
import os
import re
import threading
import time
from typing import List, Optional, Tuple

from core.config import config

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PROFILE_DIR = os.path.join(_PROJECT_ROOT, ".deepseek_profile")

_URL = "https://chat.deepseek.com/"
_INPUT_SELECTORS = ("#chat-input", "textarea")
_REPLY_SELECTOR = ".ds-markdown"
#: 回复文本连续稳定多久判定生成完成（毫秒）
_STABLE_MS = 1500


class DeepSeekWebError(RuntimeError):
    """网页版 DeepSeek 调用失败。"""


class NotLoggedInError(DeepSeekWebError):
    """未登录（未找到输入框）：需要先运行 tools/deepseek_web_login.py。"""


def _repair_unescaped_quotes(s: str) -> str:
    """修复 LLM 常见 JSON 错误：字符串值内部的双引号未转义。

    典型场景：CSS 选择器里的引号——模型常返回
        {"学生姓名":"input[name="xm"]"}
    而不是规范的
        {"学生姓名":"input[name=\\"xm\\"]"}

    判定规则：字符串遇到引号时，向后跳过空白看下一个字符——
    若是 , } : 或字符串结尾，视为**闭合引号**；否则视为内部引号并补转义。
    （有意不把 ] 作为闭合信号：选择器里 "name="xm"] 的常见形态优先。）
    仅在严格解析失败后作为兜底使用，正常 JSON 不受影响。
    """
    out: List[str] = []
    in_str = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if not in_str:
            out.append(ch)
            if ch == '"':
                in_str = True
            i += 1
            continue

        if ch == "\\":            # 已有转义序列：原样保留
            out.append(ch)
            if i + 1 < n:
                out.append(s[i + 1])
            i += 2
            continue

        if ch == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            nxt = s[j] if j < n else ""
            if nxt in (",", "}", ":", ""):
                out.append(ch)     # 闭合引号
                in_str = False
            else:
                out.append('\\"')  # 内部引号 → 补转义
            i += 1
            continue

        out.append(ch)
        i += 1
    return "".join(out)


def extract_json_block(text: str) -> dict:
    """从回复文本中提取 JSON 对象。

    处理：纯 JSON / markdown 代码块包裹 / JSON 前后带说明文字 /
    值内部双引号漏转义（LLM 常见滑手，见 _repair_unescaped_quotes）。
    """
    if not text or not text.strip():
        raise ValueError("回复为空，无法解析 JSON")

    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s).strip()

    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001 — 继续尝试容错修复
        pass

    try:
        return json.loads(_repair_unescaped_quotes(s))
    except Exception:  # noqa: BLE001 — 继续尝试截取
        pass

    i, j = s.find("{"), s.rfind("}")
    if i >= 0 and j > i:
        candidate = s[i:j + 1]
        try:
            return json.loads(candidate)
        except Exception:  # noqa: BLE001
            return json.loads(_repair_unescaped_quotes(candidate))
    raise ValueError("回复中未找到 JSON 对象")


class DeepSeekWebClient:
    """chat.deepseek.com 全自动客户端（浏览器常驻 + 串行调用）。"""

    _lock = threading.Lock()

    def __init__(self, headless: Optional[bool] = None,
                 user_data_dir: Optional[str] = None,
                 reply_timeout: Optional[float] = None):
        web_cfg = config.llm_web_config
        self.headless = (bool(web_cfg.get("headless", False))
                         if headless is None else headless)
        self.user_data_dir = user_data_dir or web_cfg.get(
            "user_data_dir") or _DEFAULT_PROFILE_DIR
        self.reply_timeout = float(
            reply_timeout or web_cfg.get("reply_timeout_seconds", 180))

        self._pw = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # 浏览器生命周期
    # ------------------------------------------------------------------
    def _ensure_page(self):
        """确保有一个可用的 chat.deepseek.com 页面（复用已有标签页）。"""
        if self._page is not None and not self._page.is_closed():
            return self._page

        from playwright.sync_api import sync_playwright

        if self._context is None:
            if self._pw is None:
                try:
                    self._pw = sync_playwright().start()
                except Exception as e:  # noqa: BLE001
                    # 典型情况：本线程已有未关闭的 sync_playwright 实例，
                    # 二次启动会报 "Sync API inside the asyncio loop"。
                    # 明确提示而不是让用户猜；由调用方决定是否重试。
                    self._pw = None
                    raise DeepSeekWebError(
                        f"Playwright 启动失败（可能有未关闭的实例）：{e}") from e
            os.makedirs(self.user_data_dir, exist_ok=True)
            self._context = self._pw.chromium.launch_persistent_context(
                self.user_data_dir,
                headless=self.headless,
                viewport={"width": 1280, "height": 860},
            )

        page = None
        for p in self._context.pages:
            if "chat.deepseek.com" in (p.url or ""):
                page = p
                break
        if page is None:
            page = self._context.pages[0] if self._context.pages \
                else self._context.new_page()
            page.goto(_URL, wait_until="domcontentloaded", timeout=60000)

        self._page = page
        return page

    def _find_input(self, page, timeout_ms: int = 15000):
        """找输入框；找不到基本等同于未登录。"""
        for sel in _INPUT_SELECTORS:
            try:
                page.wait_for_selector(sel, timeout=timeout_ms, state="visible")
                return page.locator(sel).first
            except Exception:  # noqa: BLE001 — 尝试下一个选择器
                continue
        raise NotLoggedInError(
            "未找到 DeepSeek 输入框（可能未登录或页面结构已更新）。"
            "请先运行: python tools/deepseek_web_login.py 扫码登录一次。")

    def is_logged_in(self) -> bool:
        """探测登录态（不抛异常）。"""
        page = self._ensure_page()
        try:
            self._find_input(page, timeout_ms=4000)
            return True
        except NotLoggedInError:
            return False

    def login_interactive(self, timeout_seconds: float = 300) -> bool:
        """打开浏览器等待人工扫码登录（一次性）；登录成功返回 True。"""
        page = self._ensure_page()
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                self._find_input(page, timeout_ms=3000)
                return True
            except NotLoggedInError:
                time.sleep(2)
        return False

    def close(self) -> None:
        """关闭浏览器与 Playwright（登录态已持久化到 user_data_dir）。"""
        try:
            if self._context is not None:
                self._context.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = self._context = self._page = None

    # ------------------------------------------------------------------
    # 单次问答（全自动）
    # ------------------------------------------------------------------
    def ask(self, prompt: str, images: Optional[List[str]] = None,
            timeout: Optional[float] = None) -> str:
        """发送提示词（可附图片），全自动等待并返回回复全文。"""
        with self._lock:
            page = self._ensure_page()
            inp = self._find_input(page)

            if images:
                self._attach_images(page, images)

            baseline_count, baseline_text = self._snapshot(page)

            self._fill_prompt(page, inp, prompt)
            self._submit(page, inp)

            return self._wait_reply(page, baseline_count, baseline_text,
                                    timeout or self.reply_timeout)

    # ---- 注入提示词 ----
    @staticmethod
    def _fill_prompt(page, inp, text: str) -> None:
        """写入提示词：Playwright fill（原生 setter 路径）+ 回读校验兜底。"""
        try:
            inp.fill(text)
        except Exception:  # noqa: BLE001 — 继续走兜底
            pass

        value = ""
        try:
            value = inp.input_value()
        except Exception:  # noqa: BLE001
            pass

        if value != text:
            # 兜底：原生 setter + input 事件（React 受控组件，等价 setNativeValue）
            page.evaluate(
                """([text]) => {
                    const el = document.querySelector('#chat-input')
                        || document.querySelector('textarea');
                    if (!el) throw new Error('未找到输入框');
                    const setter = Object.getOwnPropertyDescriptor(
                        HTMLTextAreaElement.prototype, 'value')?.set;
                    setter?.call(el, text);
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                }""",
                [text],
            )

    # ---- 全自动发送 ----
    @staticmethod
    def _submit(page, inp) -> None:
        """按 Enter 发送（全自动）；以「输入框被清空」作为发送成功信号。"""
        inp.click()
        inp.press("Enter")

        for _ in range(24):  # 最长约 6 秒
            try:
                if not (inp.input_value() or "").strip():
                    return
            except Exception:  # noqa: BLE001 — 输入框可能已被替换
                return
            time.sleep(0.25)

        # 兜底：点击输入框旁的发送按钮（带 svg 的可点击按钮）
        try:
            clicked = page.evaluate(
                """() => {
                    const btns = [...document.querySelectorAll('[role="button"], button')];
                    const send = btns.reverse().find(b =>
                        b.querySelector('svg') &&
                        b.getAttribute('aria-disabled') !== 'true');
                    if (send) { send.click(); return true; }
                    return false;
                }""")
            if clicked:
                time.sleep(1.0)
                return
        except Exception:  # noqa: BLE001
            pass
        raise DeepSeekWebError("发送失败：输入框未清空且未找到发送按钮")

    # ---- 附件（可选）----
    @staticmethod
    def _attach_images(page, paths: List[str]) -> None:
        """上传图片附件（等价 DataTransfer 构造 FileList）。"""
        for path in paths:
            if not os.path.exists(path):
                raise DeepSeekWebError(f"附件不存在：{path}")
            try:
                fi = page.locator('input[type="file"]').first
                fi.set_input_files(path)
            except Exception as e:  # noqa: BLE001
                raise DeepSeekWebError(
                    f"图片附加失败（页面结构可能已更新）：{e}") from e
            page.wait_for_timeout(1500)  # 等图片进入输入区

    # ---- 读取回复 ----
    @staticmethod
    def _snapshot(page) -> Tuple[int, str]:
        """当前回复节点数 + 末尾回复文本（基线嗅探用）。"""
        return tuple(page.evaluate(
            """() => {
                const nodes = document.querySelectorAll('.ds-markdown');
                const last = nodes.length
                    ? nodes[nodes.length - 1].innerText.trim() : '';
                return [nodes.length, last];
            }"""))

    def _wait_reply(self, page, baseline_count: int, baseline_text: str,
                    timeout_s: float) -> str:
        """等待新回复出现并稳定（双条件 + 1.5s 稳定性判定）。"""
        t0 = time.time()
        last = ""
        stable_since = 0.0

        while time.time() - t0 < timeout_s:
            time.sleep(1.0)
            count, text = self._snapshot(page)
            if not text:
                continue

            is_new = count > baseline_count or text != baseline_text
            if not is_new:
                continue

            if text == last:
                if not stable_since:
                    stable_since = time.time()
                elif (time.time() - stable_since) * 1000 >= _STABLE_MS:
                    return text  # 稳定 1.5 秒 → 生成完成
            else:
                last = text
                stable_since = 0.0

        raise DeepSeekWebError(f"等待回复超时（{timeout_s:.0f} 秒）")


# ----------------------------------------------------------------------
# 进程内单例（浏览器常驻，避免反复启动与重复登录）
# ----------------------------------------------------------------------
_shared_client: Optional[DeepSeekWebClient] = None
_shared_lock = threading.Lock()


def get_web_client() -> DeepSeekWebClient:
    """获取共享的网页版客户端实例。"""
    global _shared_client
    with _shared_lock:
        if _shared_client is None:
            _shared_client = DeepSeekWebClient()
        return _shared_client


def reset_web_client() -> None:
    """关闭并清空共享实例（测试/退出时用）。"""
    global _shared_client
    with _shared_lock:
        if _shared_client is not None:
            _shared_client.close()
            _shared_client = None


#: 进程退出时关闭常驻浏览器（避免 chromium 残留、避免同线程二次启动冲突）
atexit.register(reset_web_client)
