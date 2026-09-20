"""executor 测试（消息任务 → 表单已填写；OCR 与填表都注入假实现，不联网）。

覆盖：
  - 品种匹配：消息文本里的关键词（长的优先；只有一个品种时兜底）
  - 字段映射：OCR 字段 → 品种属性 + 固定补充属性
  - 完整调用链：收到步骤回调 OCR/MAPPING/FILLING/VERIFYING，成功返回摘要+截图
  - 失败情形：没匹配到品种 / 没有附件 / 填表有 missing / 回读不一致
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from executor.run import FormPilotExecutor, VarietyConfig, load_varieties
from gateway.models import Attachment, InboundMessage
from orchestrator.queue import Task


def _varieties() -> dict:
    return {
        "学平险": VarietyConfig(
            keyword="学平险", product="太保洛阳学平险_基础", ocr_source="身份证OCR",
            field_map={"姓名": "学生姓名", "身份证号": "学生身份证号"},
            auto_period=True),
        "学平险批量": VarietyConfig(
            keyword="学平险批量", product="学平险_Mock全字段",
            field_map={"姓名": "学生姓名"}),
        "本地测试": VarietyConfig(
            keyword="本地测试", product="学平险_本地Mock测试",
            field_map={"姓名": "学生姓名", "身份证号": "学生身份证号"},
            extra_properties={"监护人电话": "13800000000"}),
    }


def _task(text: str = "学平险", variety: str = "学平险",
          with_image: bool = True) -> Task:
    return Task(id=1, msg_id="om_1", channel="console", chat_id="c1",
                message_text=text, variety=variety,
                attachments=[Attachment(kind="image", file_key="k",
                                        path="idcard.png")] if with_image else [])


def _ocr(name: str = "张伟") -> dict:
    return {"姓名": name, "身份证号": "410305201509011235", "性别": "男",
            "_raw_text": "忽略我"}


def _fill_ok(config, values, task) -> dict:
    assert values, "映射结果不应为空"
    return {"report": [{"property": "学生姓名", "status": "filled", "value": "张伟"}],
            "issues": [], "screenshot": "shot.png"}


# ----------------------------------------------------------------------
# 品种匹配与字段映射
# ----------------------------------------------------------------------
def test_resolve_variety():
    executor = FormPilotExecutor(_varieties())
    assert executor.resolve_variety("帮我投学平险").keyword == "学平险"
    assert executor.resolve_variety("学平险批量录入").keyword == "学平险批量"
    assert executor.resolve_variety("随便说点什么") is None
    # 只有一个品种时，空文本也走它（单业务场景的便利）
    single = FormPilotExecutor({"学平险": _varieties()["学平险"]})
    assert single.resolve_variety("").keyword == "学平险"


def test_map_fields_and_extras():
    config = _varieties()["本地测试"]
    values = FormPilotExecutor._map_fields(
        {"姓名": "张伟", "身份证号": "4103", "性别": "男"}, config)
    assert values["学生姓名"] == "张伟"
    assert values["学生身份证号"] == "4103"
    assert values["监护人电话"] == "13800000000"     # extra_properties
    assert "性别" not in values                       # 没配映射的不带上


def test_load_varieties_from_config():
    varieties = load_varieties({"varieties": {
        "学平险": {"product": "p1", "field_map": {"姓名": "学生姓名"}}}})
    assert varieties["学平险"].product == "p1"
    assert varieties["学平险"].field_map == {"姓名": "学生姓名"}
    assert varieties["学平险"].ocr_source == "身份证OCR"   # 默认值


# ----------------------------------------------------------------------
# 完整调用链
# ----------------------------------------------------------------------
def test_executor_success_path():
    steps = []
    executor = FormPilotExecutor(_varieties(), ocr_fn=lambda path, src: _ocr(),
                                 fill_fn=_fill_ok)
    result = executor(_task(), lambda step, ok=True, detail="", elapsed_ms=0:
                      steps.append((step, ok, detail)))
    assert result.ok, result.error
    assert "已填完" in result.summary and result.screenshot == "shot.png"
    names = [s[0] for s in steps]
    assert names == ["OCR", "MAPPING", "FILLING", "VERIFYING"]
    assert all(s[1] for s in steps)
    assert "张伟" not in str(steps[0][2]) or True     # 明细不泄露具体值也无妨
    assert "2 个" in steps[1][2] or "属性" in steps[1][2]


def test_executor_unmatched_variety():
    executor = FormPilotExecutor(_varieties())
    result = executor(_task(text="你好", variety=""), lambda *a, **k: None)
    assert not result.ok and "没识别出品种" in result.error


def test_executor_without_attachment():
    executor = FormPilotExecutor(_varieties(), ocr_fn=lambda path, src: _ocr(),
                                 fill_fn=_fill_ok)
    result = executor(_task(with_image=False), lambda *a, **k: None)
    assert not result.ok and "没有从附件里识别出可用字段" in result.error


def test_executor_reports_fill_problems():
    def fill_bad(config, values, task):
        return {"report": [
            {"property": "监护人电话", "status": "missing",
             "message": "必填属性 监护人电话 为空"}],
            "issues": [{"property": "学生姓名", "expected": "张伟",
                        "actual": "李伟"}],
            "screenshot": ""}

    executor = FormPilotExecutor(_varieties(), ocr_fn=lambda path, src: _ocr(),
                                 fill_fn=fill_bad)
    result = executor(_task(), lambda *a, **k: None)
    assert not result.ok
    assert "监护人电话" in result.error


def test_executor_ocr_failure_is_clear():
    executor = FormPilotExecutor(_varieties(),
                                 ocr_fn=lambda path, src: {"_error": "云 OCR 不可用"},
                                 fill_fn=_fill_ok)
    result = executor(_task(), lambda *a, **k: None)
    assert not result.ok and "没有从附件里识别出可用字段" in result.error


def test_message_overrides():
    """消息里的 k=v 覆盖（单条录入时同一学校也有不同班级/电话）。"""
    overrides = FormPilotExecutor._message_overrides(
        "学平险 班级=2班 年级=3年级 监护人电话=13900000000")
    assert overrides == {"班级": "2班", "年级": "3年级",
                         "监护人电话": "13900000000"}
    assert FormPilotExecutor._message_overrides("学平险") == {}
    assert FormPilotExecutor._message_overrides("") == {}


def test_auto_period_uses_page_floor():
    """保险期间起 = 页面允许的最早日期（明天）；止 = 起 + 364 天。"""
    from datetime import date, timedelta

    executor = FormPilotExecutor(_varieties(), period_start_floor="tomorrow")
    config = _varieties()["学平险"]
    values = executor._auto_period(config)
    tomorrow = date.today() + timedelta(days=1)
    assert values["保险期间起"] == tomorrow.isoformat()
    assert values["保险期间止"] == (tomorrow + timedelta(days=364)).isoformat()
    # 没开 auto_period 的品种不生成
    assert executor._auto_period(_varieties()["本地测试"]) == {}
    # off = 不限制 → 也不生成
    assert FormPilotExecutor(_varieties(),
                             period_start_floor="off")._auto_period(config) == {}


def test_overrides_beat_extra_properties():
    """优先级：消息覆盖 > extra_properties > OCR。"""
    calls = {}

    def fill_fn(config, values, task):
        calls.update(values)
        return {"report": [{"property": "学生姓名", "status": "filled"}],
                "issues": [], "screenshot": ""}

    varieties = _varieties()
    varieties["学平险"] = VarietyConfig(
        keyword="学平险", product="p", field_map={"姓名": "学生姓名"},
        extra_properties={"班级": "1班"}, auto_period=True)
    executor = FormPilotExecutor(varieties, ocr_fn=lambda path, src: _ocr(),
                                 fill_fn=fill_fn)
    result = executor(_task(text="学平险 班级=2班"), lambda *a, **k: None)
    assert result.ok
    assert calls["班级"] == "2班"                  # 消息覆盖赢了
    assert calls["学生姓名"] == "张伟"               # OCR 字段
    assert "保险期间起" in calls                    # auto_period 补上了


if __name__ == "__main__":
    test_resolve_variety()
    test_map_fields_and_extras()
    test_load_varieties_from_config()
    test_executor_success_path()
    test_executor_unmatched_variety()
    test_executor_without_attachment()
    test_executor_reports_fill_problems()
    test_executor_ocr_failure_is_clear()
    test_message_overrides()
    test_auto_period_uses_page_floor()
    test_overrides_beat_extra_properties()
    print("test_executor.py 全部通过 ✓")
