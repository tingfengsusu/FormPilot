"""填表会话：一条浏览器会话连续填多条记录（规格 §三 执行协议）。

职责边界（守卫测试会检查）：

- **引擎**：开/关浏览器、逐条填写（含重试）、回读评估、截图、（可选）提交、发事件
- **业务**：取记录、算值（属性池）、决定截图路径、写状态机与台账

Mock 模式（``mock=True``）不建浏览器：走 ``FormFiller.fill()`` 的模拟路径，
供没有浏览器的环境与测试使用。
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .blockers import detect_blocker
from .models import BAD_STATUSES, FillEvent, FillResult, OpenSpec

#: 单条问题的截断长度：点击超时日志很长，不截断会把后面的问题挤掉（实测只看到第一条）
_PROBLEM_LIMIT = 160

#: 事件回调：``(事件, 数据)``；回调异常不影响填写
EventHook = Callable[[FillEvent, Dict[str, Any]], None]


def safe_filename(name: str, fallback: str = "record") -> str:
    """文件名安全化（去掉 Windows 非法字符）。"""
    cleaned = "".join(ch for ch in (name or "") if ch not in '<>:"/\\|?*').strip()
    return cleaned or fallback


def problems_of(report: Any) -> List[str]:
    """从填充报告里挑出问题（error / missing / mismatch），截断后返回。"""
    problems: List[str] = []
    for entry in report or []:
        status = str(entry.get("status") or "")
        if status in BAD_STATUSES:
            message = str(entry.get("message") or status)
            problems.append(f"[{entry.get('property')}] {message[:_PROBLEM_LIMIT]}")
    return problems


def capture_page(page: Any, path: str) -> str:
    """给页面对象截图并落盘；无页面或失败 → 空串（不改变填写结果）。"""
    if page is None:
        return ""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        page.screenshot(path=path, full_page=False)
        return path
    except Exception:  # noqa: BLE001
        return ""


class FillSession:
    """一次浏览器会话：可连续填多条记录（串行）。"""

    def __init__(self, product: Any, *, headless: bool = False,
                 mock: bool = False,
                 filler_factory: Optional[Callable[..., Any]] = None,
                 on_event: Optional[EventHook] = None):
        self.product = product
        self.headless = headless
        self.mock = mock
        self.filler_factory = filler_factory
        self.on_event = on_event
        self.open_spec = OpenSpec.from_product(product)
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def open(self) -> "FillSession":
        """起浏览器会话（Mock 模式只发事件，不建浏览器）。"""
        self._emit(FillEvent.SESSION_STARTED, mock=self.mock, url=self.open_spec.url)
        if self.mock:
            return self
        from playwright.sync_api import sync_playwright

        from page.browser_context import create_context

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            context = create_context(self._browser, self.open_spec.context,
                                     target_url=self.open_spec.url)
            self._page = context.new_page()
        except Exception:
            self.close()
            raise
        return self

    def close(self) -> None:
        """关浏览器会话（幂等）。"""
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001 — 关浏览器失败不应掩盖业务结果
            pass
        finally:
            if self._pw is not None:
                try:
                    self._pw.stop()
                except Exception:  # noqa: BLE001
                    pass
            self._browser = self._page = self._pw = None
            self._emit(FillEvent.SESSION_CLOSED)

    def __enter__(self) -> "FillSession":
        return self.open()

    def __exit__(self, *exc_info) -> bool:
        self.close()
        return False

    @property
    def page(self) -> Any:
        """当前页面对象（批注/复核等辅助能力需要）；未开浏览器时为 None。"""
        return self._page

    # ------------------------------------------------------------------
    # 填一条（含重试；回读评估来自填充报告）
    # ------------------------------------------------------------------
    def fill(self, pool: Any, *, submit: bool = False,
             screenshot_path: str = "", max_attempts: int = 1,
             backoff_seconds: float = 0.0, label: str = "") -> FillResult:
        """填一条记录：打开表单 → 逐字段填写（含回读）→（可选）提交 → 截图。

        :param screenshot_path: 成功时截图落盘路径（空 = 不截图；失败不截图）
        :param max_attempts: 最大尝试次数（含首次）
        :param backoff_seconds: 重试退避基数（第 n 次重试等待 backoff * 2^(n-1)）
        """
        result = FillResult()
        self._emit(FillEvent.RECORD_STARTED, label=label)
        problems: List[str] = []

        for attempt in range(1, max(1, int(max_attempts)) + 1):
            result.attempts = attempt
            blocker = ""
            try:
                report, blocker = self._fill_once(pool, submit=submit)
                problems = problems_of(report)
            except Exception as e:  # noqa: BLE001 — 单条失败不影响同批其他条
                report, problems = [], [f"填表异常：{e}"]
                result.error = str(e)
            result.report = report
            if blocker:
                # 验证码/登录墙：重试只会再遇到同一个，直接转人工（不填、不提交）
                result.needs_human = blocker
                result.problems = []
                self._emit(FillEvent.BLOCKED, label=label, reason=blocker)
                break
            self._emit(FillEvent.FIELD_VERIFIED, label=label, attempt=attempt,
                       filled=sum(1 for e in report if e.get("status") == "filled"),
                       problems=len(problems))
            for entry in report:
                if entry.get("status") == "mismatch":
                    self._emit(FillEvent.DIFF_FOUND, label=label,
                               property=entry.get("property"),
                               message=str(entry.get("message") or ""))
            if not problems:
                result.ok = True
                result.submitted = bool(submit)
                if screenshot_path:
                    result.screenshot = self.capture(screenshot_path)
                break
            if attempt < max_attempts and backoff_seconds > 0:
                time.sleep(backoff_seconds * (2 ** (attempt - 1)))

        result.problems = problems
        result.diffs = [e for e in result.report if e.get("status") == "mismatch"]
        self._emit(FillEvent.RECORD_FINISHED, label=label, ok=result.ok,
                   attempts=result.attempts)
        return result

    def submit_now(self) -> None:
        """点提交按钮 —— 仅在调用方显式要求时执行（默认不提交）。

        提交前**先清掉批注层**：批注是给人看的浮层，绝不能跟着表单提交出去。
        """
        from page.overlay import click_with_guard

        if self._page is not None:
            from .annotate import clear_annotations
            clear_annotations(self._page)
        click_with_guard(self._page, self.product.submit_button, timeout=5000)
        self._page.wait_for_timeout(1500)
        self._emit(FillEvent.SUBMITTED, url=self.open_spec.url)

    def capture(self, path: str) -> str:
        """截图落盘；失败返回空串（截图失败不改变填写结果）。"""
        return capture_page(self._page, path)

    def annotate(self, pool: Any, issues: Optional[Sequence[dict]] = None) -> int:
        """把「期望值」贴到当前页面上（复核用）；返回贴上的条数。

        只在"人正看着这个页面"的场合用（单条录入 / 逐条确认）；
        批量无人值守时不贴 —— 那条路径的复核靠截图与对比报告。
        """
        if self._page is None:
            return 0
        from .annotate import annotate_expected

        return annotate_expected(self._page, self.product, pool, issues=issues)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _fill_once(self, pool: Any, *, submit: bool) -> Tuple[List[Dict[str, Any]], str]:
        """填一次表单（Mock / 无页面 → 模拟填写路径）。

        :return: (填充报告, 需人工的原因) —— 页面被验证码/登录墙拦住时报告为空、
            原因非空，调用方据此转人工（不填、不提交）。
        """
        if self.filler_factory is not None:
            filler = self.filler_factory(self.product, pool)
        else:
            from page.filler import FormFiller

            filler = FormFiller(self.product, pool, browser_hold_seconds=0)

        if self.mock or self._page is None:
            fill_mock = getattr(filler, "fill_mock", None)
            return (fill_mock() if callable(fill_mock)
                    else filler.fill(headless=True)), ""

        filler.open_form(self._page)
        self._emit(FillEvent.FORM_OPENED, url=self.open_spec.url)
        blocker = detect_blocker(self._page)
        if blocker:
            return [], blocker
        report = filler.fill_on_page(self._page)
        # 保持既有行为：提交发生在回读评估之前（收紧时机需要业务侧确认）
        if submit:
            self.submit_now()
        return report, ""

    def _emit(self, event: FillEvent, **payload: Any) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(event, payload)
        except Exception:  # noqa: BLE001 — 观察者异常不影响填写
            pass
