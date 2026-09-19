"""batch/probe.py + tools/probe_options.py 测试（假页面，不依赖浏览器）。

覆盖：
  - 选项文件写出/读回（含级联分组的 by_selector）
  - 别名表核对：下拉字段目标值必须在选项集合内；文本字段只做提示
  - 定位：品种配置优先；自动识别命中 / 歧义不猜 / 找不到时给出手工指定指引
  - 控件类型：文本输入（学校名称）不当作失败；下拉字段只认到输入框时报错
  - 级联：逐个选择「学生类别」的选项，分别抓年级/班级并记录分组
  - 生成可粘贴的 field_mapping 片段
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from batch import probe as probe_mod
from batch.probe import (ALL_FIELDS, PROBE_FIELDS, FieldProbeResult, ProbeField,
                         check_alias_targets, field_mapping_snippet,
                         format_report, load_by_selector, load_probed_options,
                         probe_all, write_options_file)

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

SCHOOL_FIELD = PROBE_FIELDS[0]      # 学校名称（文本输入）
GRADE_FIELD = PROBE_FIELDS[1]       # 年级（下拉，随学生类别变化）
CLASS_FIELD = PROBE_FIELDS[2]       # 班级


class _FakeProduct:
    def __init__(self, mapping):
        self.field_mapping = mapping


class _FakeMapping:
    def __init__(self, property, locator):
        self.property, self.locator = property, locator


class _FakeLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def evaluate(self, js):
        if "tagName" in js:
            return self.page.tags.get(self.selector, "div")
        if "querySelector" in js:      # _is_dropdownish
            return self.page.dropdownish.get(self.selector, True)
        return None

    def click(self, timeout=None):
        self.page.clicks.append(self.selector)


class _FakePage:
    """只实现探针用到的最小页面接口。"""

    def __init__(self, candidates=None, options=None, tags=None, dropdownish=None):
        self.candidates = candidates or []
        self.options = options or {}
        self.tags = tags or {}
        self.dropdownish = dropdownish or {}
        self.clicks = []

    def evaluate(self, js, arg=None):
        if arg is None and "cssPath" in js:
            return self.candidates
        return None

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def wait_for_timeout(self, ms):
        pass


def _result(field=SCHOOL_FIELD, locator="#xx", kind="native",
            options=None) -> FieldProbeResult:
    result = FieldProbeResult(field=field, locator=locator, kind=kind)
    result.options = list(options or ["柳集小学", "柳集中学"])
    return result


# ----------------------------------------------------------------------
# 选项文件
# ----------------------------------------------------------------------
def test_write_and_load_options_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "options_school.yaml")
        write_options_file(path, _result(), product_name="测试品种",
                           url="https://example.com/x", probed_at="2026-09-18 10:00:00")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        assert "不要手改" in text and "原生 <select>" in text
        data = yaml.safe_load(text)
        assert data["options"] == ["柳集小学", "柳集中学"]
        assert data["probe"]["widget"] == "native"
        assert data["probe"]["count"] == 2


def test_write_text_field_file():
    """文本输入字段：文件写出来要说清「没有下拉选项」+ 规范名从哪来。"""
    with tempfile.TemporaryDirectory() as td:
        result = FieldProbeResult(field=SCHOOL_FIELD, locator="#xx", kind="text")
        path = os.path.join(td, "options_school.yaml")
        write_options_file(path, result, product_name="测试品种")
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        assert "文本输入" in text and "school_aliases.yaml" in text
        assert yaml.safe_load(text)["options"] == []


def test_write_and_load_by_selector():
    """级联分组要写进 probe.by_selector，并能读回来。"""
    with tempfile.TemporaryDirectory() as td:
        result = _result(field=GRADE_FIELD, locator="#gradeBox", kind="custom",
                         options=["1年级", "初中一年级"])
        result.by_selector = {"小学生": ["1年级"], "中学生": ["初中一年级"]}
        write_options_file(os.path.join(td, "options_grade.yaml"), result)
        groups = load_by_selector(td)
        assert groups == {"grade": {"小学生": ["1年级"], "中学生": ["初中一年级"]}}


def test_load_probed_options_reads_real_config():
    probed = load_probed_options(CONFIG_DIR)
    assert set(probed) == {f.key for f in ALL_FIELDS}
    for values in probed.values():
        assert isinstance(values, list)


def test_check_alias_targets():
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "school_aliases.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"aliases": {"柳小": "柳集小学", "清花园": "清风园小学"}},
                           f, allow_unicode=True)
        # 选项已产出：一个命中、一个不在集合内
        problems = check_alias_targets({"school": ["柳集小学"]}, td)
        assert len(problems) == 1 and "清风园小学" in problems[0]

        # 选项未产出但别名表非空 → 明确报「没有选项可核对」
        problems2 = check_alias_targets({"school": []}, td)
        assert len(problems2) == 1 and "没有选项可核对" in problems2[0]

        # 选项已产出且目标值都在集合内 → 无问题
        assert check_alias_targets({"school": ["柳集小学", "清风园小学"]}, td) == []


def test_check_alias_targets_text_field():
    """文本输入字段：没有选项可核对 → 只提示；别名表为空才是问题。"""
    with tempfile.TemporaryDirectory() as td:
        kinds = {"school": "text"}
        notes: list = []
        problems = check_alias_targets({"school": []}, td, kinds=kinds, notes=notes)
        assert problems and "别名表为空" in problems[0]

        with open(os.path.join(td, "school_aliases.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump({"aliases": {"柳小": "柳集小学"}}, f, allow_unicode=True)
        notes2: list = []
        problems2 = check_alias_targets({"school": []}, td, kinds=kinds, notes=notes2)
        assert problems2 == [] and notes2 and "人工确认" in notes2[0]


# ----------------------------------------------------------------------
# 定位
# ----------------------------------------------------------------------
def test_detect_locators_from_product_first():
    product = _FakeProduct([
        _FakeMapping("学校名称", 'input[placeholder="请录入学校名称"]'),
        _FakeMapping("学生姓名", 'input[name="xm"]'),
    ])
    results = probe_mod.detect_locators(_FakePage(), product)
    assert results["school"].locator == 'input[placeholder="请录入学校名称"]'
    assert results["school"].source == probe_mod.SOURCE_PRODUCT
    # 未配置的字段回落到自动识别（这里页面没有候选 → 明确报找不到）
    assert "未找到" in results["grade"].error


def test_detect_locators_auto_and_ambiguity():
    page = _FakePage(candidates=[
        {"kind": "input", "tag": "input", "locator": "#xx", "depth": 5,
         "dropdownish": False, "labels": ["请录入学校名称"], "text": ""},
        {"kind": "custom", "tag": "div", "locator": 'div.ant-select >> nth=0',
         "depth": 8, "dropdownish": True, "labels": ["请录入年级"], "text": ""},
        # 两个都自称「班级」→ 歧义，不猜
        {"kind": "custom", "tag": "div", "locator": "#classA", "depth": 9,
         "dropdownish": True, "labels": ["班级"], "text": "班级"},
        {"kind": "custom", "tag": "div", "locator": "#classB", "depth": 9,
         "dropdownish": True, "labels": ["班级"], "text": "班级"},
    ])
    results = probe_mod.detect_locators(page)
    assert results["school"].locator == "#xx"
    assert results["school"].kind == "text"
    assert results["grade"].locator == 'div.ant-select >> nth=0'
    assert "无法确定" in results["class_name"].error
    assert "--class" in results["class_name"].error
    assert len(results["class_name"].candidates) == 2


def test_pick_candidate_prefers_dropdown_and_innermost():
    """实测两条打分规则：antd 组件根优先；同一标签的嵌套 div 取最内层。"""
    nested_same_label = [
        {"kind": "custom", "tag": "div", "locator": "div.outer", "depth": 6,
         "dropdownish": False, "labels": ["请录入学校名称"]},
        {"kind": "custom", "tag": "div", "locator": "div.school-select", "depth": 9,
         "dropdownish": False, "labels": ["请录入学校名称"]},
    ]
    picked = probe_mod._pick_candidate(nested_same_label, SCHOOL_FIELD)
    assert picked["locator"] == "div.school-select"

    antd_like = [
        {"kind": "custom", "tag": "div", "locator": "div.ant-select-inner", "depth": 12,
         "dropdownish": False, "labels": ["请录入年级", "请录入年级1年级"]},
        {"kind": "custom", "tag": "div", "locator": "div.ant-select", "depth": 9,
         "dropdownish": True, "labels": ["请录入年级1年级"]},
    ]
    picked2 = probe_mod._pick_candidate(antd_like, GRADE_FIELD)
    assert picked2["locator"] == "div.ant-select"      # 有下拉特征的优先


def test_default_kind_does_not_accept_text_input():
    """年级/班级预期是下拉：只找到文本控件时要报错（不猜、不静默放过）。"""
    page = _FakePage(candidates=[
        {"kind": "custom", "tag": "div", "locator": "#gradeBox", "depth": 8,
         "dropdownish": False, "labels": ["年级"], "text": ""},
        {"kind": "input", "tag": "input", "locator": "#xx", "depth": 5,
         "dropdownish": False, "labels": ["请录入学校名称"], "text": ""},
    ])
    results = probe_mod.detect_locators(page)
    assert not results["grade"].ok
    assert "与预期不符" in results["grade"].error
    # 学校名称允许文本控件（实测就是 div.school-select）
    assert results["school"].locator == "#xx"


def test_detect_locators_manual_override_wins():
    product = _FakeProduct([_FakeMapping("学校名称", "#fromProduct")])
    results = probe_mod.detect_locators(_FakePage(), product)
    patched = probe_all(_FakePage(), product,
                        overrides={"school": "#fromCli"}, interval_seconds=0)
    assert results["school"].locator == "#fromProduct"
    assert patched["school"].locator == "#fromCli"
    assert patched["school"].source == probe_mod.SOURCE_MANUAL


# ----------------------------------------------------------------------
# 抓选项
# ----------------------------------------------------------------------
def test_probe_field_text_input():
    page = _FakePage(tags={"#xx": "input"})
    result = FieldProbeResult(field=SCHOOL_FIELD, locator="#xx")
    probe_mod.probe_field(page, result)
    assert result.is_text and result.ok and not result.error
    assert result.options == []
    assert "文本输入" in result.summary_line()


def test_probe_field_text_input_for_choice_field_is_error():
    page = _FakePage(tags={"#gradeBox": "input"})
    result = FieldProbeResult(field=GRADE_FIELD, locator="#gradeBox")
    probe_mod.probe_field(page, result)
    assert not result.ok and "与预期不符" in result.error


def test_probe_all_with_fake_collect():
    calls = []

    def fake_collect(page, locator):
        calls.append(locator)
        if locator == "#gradeBox":
            return {"kind": "custom", "options": ["1年级", "2年级"], "raw": []}
        return {"kind": "unknown", "options": [], "raw": []}

    original = probe_mod.collect_options
    probe_mod.collect_options = fake_collect
    try:
        page = _FakePage(
            candidates=[
                {"kind": "input", "tag": "input", "locator": "#xx",
                 "labels": ["请录入学校名称"], "text": ""},
                {"kind": "custom", "tag": "div", "locator": "#gradeBox",
                 "labels": ["年级"], "text": "年级"},
            ],
            tags={"#xx": "input"})
        results = probe_all(page, None, interval_seconds=0)
    finally:
        probe_mod.collect_options = original

    # 学校是文本输入（不抓选项）；年级抓一次（页面上没有学生类别可选）
    assert "#xx" not in calls
    assert calls == ["#gradeBox"]
    school = results["school"]
    assert school.is_text and school.ok
    grade = results["grade"]
    assert grade.ok and grade.kind == "custom" and len(grade.options) == 2
    assert "2 个选项" in grade.summary_line()
    assert not results["class_name"].ok     # 页面没有班级字段


def test_probe_cascade_groups_by_selector():
    """级联：逐个选择学生类别，分别抓年级选项并记录分组。"""
    state = {"level": ""}
    grade_options = {
        "小学生": ["1年级", "2年级"],
        "中学生": ["初中一年级"],
    }

    def fake_collect(page, locator):
        if locator == "#levelBox":
            return {"kind": "custom",
                    "options": ["请选择", "小学生", "中学生"], "raw": []}
        return {"kind": "custom", "options": grade_options.get(state["level"], []),
                "raw": []}

    def fake_select(page, locator, value):
        state["level"] = value
        return value

    original_collect, original_select = probe_mod.collect_options, probe_mod.select_value
    probe_mod.collect_options = fake_collect
    probe_mod.select_value = fake_select
    try:
        page = _FakePage(
            candidates=[
                {"kind": "custom", "tag": "div", "locator": "#levelBox",
                 "labels": ["学生类别"], "text": "学生类别"},
                {"kind": "custom", "tag": "div", "locator": "#gradeBox",
                 "labels": ["年级"], "text": "年级"},
            ],
            tags={"#levelBox": "div", "#gradeBox": "div"})
        results = probe_all(page, None, interval_seconds=0)
    finally:
        probe_mod.collect_options = original_collect
        probe_mod.select_value = original_select

    level = results["student_level"]
    assert level.options == ["请选择", "小学生", "中学生"]

    grade = results["grade"]
    assert grade.by_selector == {"小学生": ["1年级", "2年级"],
                                 "中学生": ["初中一年级"]}
    assert grade.options == ["1年级", "2年级", "初中一年级"]   # 全集（保持顺序）
    assert "请选择" not in grade.options
    assert "选项随「学生类别」变化" in grade.note
    # 占位项「请选择」不参与选择
    assert state["level"] == "中学生"


def test_probe_cascade_disabled():
    def fake_collect(page, locator):
        if locator == "#levelBox":
            return {"kind": "custom", "options": ["小学生"], "raw": []}
        return {"kind": "custom", "options": ["1年级"], "raw": []}

    original = probe_mod.collect_options
    probe_mod.collect_options = fake_collect
    try:
        page = _FakePage(
            candidates=[
                {"kind": "custom", "tag": "div", "locator": "#levelBox",
                 "labels": ["学生类别"], "text": "学生类别"},
                {"kind": "custom", "tag": "div", "locator": "#gradeBox",
                 "labels": ["年级"], "text": "年级"},
            ])
        results = probe_all(page, None, interval_seconds=0, cascade=False)
    finally:
        probe_mod.collect_options = original
    assert results["grade"].by_selector == {}       # 没做分组
    assert results["grade"].options == ["1年级"]


def test_format_report_and_snippet():
    results = {field.key: FieldProbeResult(field=field) for field in ALL_FIELDS}
    results["school"] = _result(field=SCHOOL_FIELD, kind="text", options=[])
    results["school"].is_text and None
    results["grade"] = _result(field=GRADE_FIELD, locator="#gradeBox",
                               kind="custom", options=["1年级"])
    results["grade"].by_selector = {"小学生": ["1年级"]}
    report = format_report(results, "测试品种")
    assert "学校名称：文本输入（无下拉选项）" in report
    assert "年级：1 个选项（自定义组件）" in report
    assert "小学生 → 1 个" in report

    snippet = field_mapping_snippet(results)
    assert "- property: 学校名称" in snippet
    assert "- property: 年级" in snippet and "fill_mode: select" in snippet
    assert "- property: 班级" not in snippet    # 没抓到的字段不进片段


if __name__ == "__main__":
    test_write_and_load_options_roundtrip()
    test_write_text_field_file()
    test_write_and_load_by_selector()
    test_load_probed_options_reads_real_config()
    test_check_alias_targets()
    test_check_alias_targets_text_field()
    test_detect_locators_from_product_first()
    test_detect_locators_auto_and_ambiguity()
    test_default_kind_does_not_accept_text_input()
    test_detect_locators_manual_override_wins()
    test_probe_field_text_input()
    test_probe_field_text_input_for_choice_field_is_error()
    test_probe_all_with_fake_collect()
    test_probe_cascade_groups_by_selector()
    test_probe_cascade_disabled()
    test_format_report_and_snippet()
    print("test_batch_probe.py 全部通过 ✓")
