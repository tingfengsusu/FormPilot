"""页面探针端到端（本地 Mock 页，真实 Playwright）。

Mock 页（tests/mock_probe_options.html）按**真实页面实测结构**搭的：
  - 学校名称是**文本输入**（没有下拉）→ 探针只报告类型
  - 学生类别是**自定义组件**下拉；年级/班级是下拉，**选项随学生类别变化**
  - 班级选项形如 1班 / 2班 / …

未安装 Playwright / 浏览器不可用时自动跳过。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

MOCK_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "mock_probe_options.html")


def test_probe_local_mock_page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        skip_test("未安装 Playwright")
        return

    from batch import probe as probe_mod
    from core.product import Product
    from core.property_pool import PropertyPool
    from page.browser_context import create_context
    from page.filler import FormFiller

    url = "file:///" + MOCK_PAGE.replace("\\", "/")

    try:
        playwright = sync_playwright().start()
    except Exception as e:  # noqa: BLE001
        skip_test(f"Playwright 不可用：{e}")
        return
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        playwright.stop()
        skip_test(f"Chromium 未安装：{e}")
        return

    try:
        page = create_context(browser, None, target_url=url).new_page()
        FormFiller(Product(name="probe-test", target_url=url),
                   PropertyPool()).open_form(page)
        results = probe_mod.probe_all(page, None, interval_seconds=0)
    finally:
        browser.close()
        playwright.stop()

    # 学校名称：文本输入，没有下拉选项 → 不是失败
    school = results["school"]
    assert school.is_text, school.kind
    assert school.ok and not school.error
    assert school.options == []

    # 学生类别：自定义组件下拉
    level = results["student_level"]
    assert level.ok and level.kind == "custom", (level.error, level.kind)
    assert level.options == ["小学生", "中学生", "大中专学校学生"]

    # 年级：级联——每个学生类别下各有一份选项，options 是全集
    grade = results["grade"]
    assert grade.ok, grade.error
    assert grade.kind == "custom", grade.kind
    assert grade.by_selector["小学生"] == ["1年级", "2年级", "3年级",
                                           "4年级", "5年级", "6年级"]
    assert grade.by_selector["中学生"] == [
        "初中一年级", "初中二年级", "初中三年级",
        "高中一年级", "高中二年级", "高中三年级"]
    assert grade.by_selector["大中专学校学生"] == [
        "中专一年级", "中专二年级", "大专一年级", "大专二年级"]
    assert len(grade.options) == len({
        o for options in grade.by_selector.values() for o in options})

    # 班级：原生 select，选项也按学生类别分组
    class_name = results["class_name"]
    assert class_name.ok and class_name.kind == "native", \
        (class_name.error, class_name.kind)
    assert class_name.by_selector["小学生"] == ["1班", "2班", "3班",
                                                "4班", "5班", "6班"]
    assert class_name.by_selector["中学生"] == [
        "1班", "2班", "3班", "4班", "5班", "6班", "7班"]
    assert class_name.by_selector["大中专学校学生"] == ["1班", "2班", "3班"]

    # 报告里必须出现「选项数量 + 控件类型 + 级联分组」
    report = probe_mod.format_report(results, "Mock探针页")
    assert "文本输入（无下拉选项）" in report
    assert "自定义组件" in report and "原生 <select>" in report
    assert "小学生 → 6 个" in report

    # 文本字段不该被写成下拉字段的填法
    snippet = probe_mod.field_mapping_snippet(results)
    assert "- property: 学校名称" in snippet
    assert "学生类别" in snippet            # 级联提示
    assert snippet.count("fill_mode: select") == 2   # 年级 + 班级


if __name__ == "__main__":
    test_probe_local_mock_page()
    print("test_batch_probe_page.py 全部通过 ✓")
