"""网页版 DeepSeek 在线测试（真实浏览器自动化调用）。

覆盖：
  1. DeepSeekWebClient.ask：真实完成「开浏览器 → 注入 → 按 Enter 发送 →
     嗅探回复 → 稳定判定 → 读取文本」全自动流程，断言能拿到合法 JSON
  2. FieldMatcher 经网页版 LLM 做题干匹配：验证项目真实集成点
     （字段名 → locator 映射全部正确）

跳过条件（环境问题不计失败）：未装 Playwright / 浏览器不可用 /
未登录 / 网页版超时。
注意：每次运行会打开无头浏览器并各消耗一次 DeepSeek 网页版问答（约 20-40 秒）。
不想跑在线用例时：pytest tests/ -q -k "not online"
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _force_web_mode() -> None:
    """固定 llm.mode=web + headless（测试不开可见窗口），确保走真实网页版通道。

    注意：客户端用进程内共享单例（浏览器常驻），进程退出时由 atexit 自动关闭；
    不在用例内部反复关闭/重启 —— 同线程二次启动 sync_playwright 会冲突。
    """
    from core import config as config_module
    config_module.config._data = {
        "mock_mode": False,
        "llm": {"mode": "web", "web": {"headless": True}},
    }


def _skip_if_env_error(e: Exception) -> bool:
    """未登录 / 超时等环境问题 → 跳过；返回 True 表示已跳过。"""
    from llm.web_client import NotLoggedInError

    if isinstance(e, NotLoggedInError):
        skip_test(f"网页版未登录（运行 tools/deepseek_web_login.py 登录一次）：{e}")
        return True
    msg = str(e)
    if any(m in msg for m in ("超时", "timeout", "Timeout", "connect", "Connect")):
        skip_test(f"网页版环境不可用（{msg[:80]}）")
        return True
    return False


def test_web_llm_ask_roundtrip():
    """真实网页版问答：ask() 全自动返回可解析的 JSON。"""
    if not _playwright_available():
        skip_test("未安装 playwright")
        return

    _force_web_mode()
    from llm.web_client import get_web_client, extract_json_block, reset_web_client

    try:
        client = get_web_client()   # 共享单例：整个进程复用同一个浏览器
        reply = client.ask(
            '请只返回一个 JSON 对象，不要任何其他文字：{"ok": true}',
            timeout=90)
    except Exception as e:  # noqa: BLE001
        if _skip_if_env_error(e):
            return
        raise
    finally:
        # 用完即关：常驻浏览器会占用本线程的 Playwright，
        # 影响同进程后续使用 Playwright 的测试/功能
        reset_web_client()

    data = extract_json_block(reply)
    assert data.get("ok") is True, f"网页版回复异常: {reply[:200]}"
    print(f"✓ 网页版 DeepSeek 全自动问答：{reply[:60]}")


def test_matcher_via_web_llm():
    """真实集成点：FieldMatcher 经网页版 LLM 完成字段匹配。"""
    if not _playwright_available():
        skip_test("未安装 playwright")
        return

    _force_web_mode()
    from page.matcher import FieldMatcher
    from page.scanner import Scanner
    from llm.web_client import reset_web_client

    fields = Scanner()._mock_fields()   # 4 个示例页面字段
    props = ["学生姓名", "学生身份证号", "监护人电话", "保险方案"]

    matcher = FieldMatcher()
    try:
        mapping = matcher.match_via_llm(fields, props)
    except Exception as e:  # noqa: BLE001
        if _skip_if_env_error(e):
            return
        raise
    finally:
        reset_web_client()

    assert mapping["学生姓名"] == 'input[name="xm"]', f"映射错误: {mapping}"
    assert mapping["学生身份证号"] == 'input[name="sfzh"]', f"映射错误: {mapping}"
    assert mapping["监护人电话"] == 'input[name="lxdh"]', f"映射错误: {mapping}"
    assert mapping["保险方案"] == 'select[name="baoxian"]', f"映射错误: {mapping}"
    print("✓ 网页版 LLM 字段匹配：4/4 全部正确")


if __name__ == "__main__":
    test_web_llm_ask_roundtrip()
    test_matcher_via_web_llm()

    # 脚本模式下主动关闭常驻浏览器（pytest/应用由 atexit 兜底）
    try:
        from llm.web_client import reset_web_client
        reset_web_client()
    except Exception:  # noqa: BLE001
        pass
    print("test_online_web_llm.py 全部通过 ✓")
