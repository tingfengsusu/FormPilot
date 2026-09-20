"""提交路径测试（本地 mock 页，真实 Playwright；不碰真实投保页）。

设计要点：**程序只点「提交」那一下**——提交之后是手写签名（投保人的真实
意思表示，程序绝不代签），支付同理。所以：

  - `submit=False`（默认）：只填表 + 截图，绝不动提交按钮
  - `submit=True`：填表 → 点提交 → 截（签名）页 → 回推，剩下的由人做
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

MOCK_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "mock_form.html")


def _executor(**kwargs):
    from executor.run import FormPilotExecutor, VarietyConfig

    variety = VarietyConfig(
        keyword="本地测试", product="学平险_本地Mock测试",
        field_map={"姓名": "学生姓名", "身份证号": "学生身份证号"},
        extra_properties={"监护人姓名": "演示家长", "监护人电话": "13800000000",
                          "保险方案": "基础版（100元）"})
    return FormPilotExecutor({"本地测试": variety},
                             screenshot_dir=kwargs.pop("screenshot_dir",
                                                       "data/screenshots/tests"),
                             ocr_fn=lambda path, src: {"姓名": "张伟",
                                                       "身份证号": "410305201509011235"},
                             **kwargs)


def _task():
    from gateway.models import Attachment
    from orchestrator.queue import Task

    return Task(id=4242, msg_id="m-submit", channel="console", chat_id="c",
                message_text="本地测试", variety="本地测试",
                attachments=[Attachment(kind="image", file_key="k",
                                        path="idcard.png")])


def _browser_or_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        skip_test("未安装 Playwright")
        return None, None
    try:
        playwright = sync_playwright().start()
    except Exception as e:  # noqa: BLE001
        skip_test(f"Playwright 不可用：{e}")
        return None, None
    try:
        # 用真实产品配置（指向 mock 页），确保 submit_button 从 yaml 读
        return playwright, None
    except Exception as e:  # noqa: BLE001
        playwright.stop()
        skip_test(f"浏览器不可用：{e}")
        return None, None


def test_submit_disabled_by_default():
    """默认不提交：摘要里没有「已提交」，也没有 SUBMITTED 步骤。"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        skip_test("未安装 Playwright")
        return

    steps = []
    executor = _executor()
    result = executor(_task(), lambda step, ok=True, detail="", elapsed_ms=0:
                      steps.append(step))
    assert result.ok, result.error
    assert "已填完" in result.summary and "已提交" not in result.summary
    assert "SUBMITTED" not in steps


def test_submit_true_clicks_and_reports_next_step():
    """submit=True：点提交 → 摘要提示「请完成手写签名与支付」+ SUBMITTED 步骤。"""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except ImportError:
        skip_test("未安装 Playwright")
        return

    steps = []
    executor = _executor()
    result = executor(_task(), lambda step, ok=True, detail="", elapsed_ms=0:
                      steps.append((step, ok)), submit=True)
    assert result.ok, result.error
    assert "已提交" in result.summary
    assert "手写签名" in result.summary          # 明确告诉人下一步要做什么
    assert ("SUBMITTED", True) in steps
    # mock 页点提交后会出现「提交预览」——截图里能看到（这里只校验截图产出）
    assert result.screenshot.endswith(".png")


if __name__ == "__main__":
    test_submit_disabled_by_default()
    test_submit_true_clicks_and_reports_next_step()
    print("test_executor_submit.py 全部通过 ✓")
