"""Bug 修复回归测试（对应 ll.txt 分析出的问题）。

覆盖：
  1. Checker 跳过空值（None / ""）属性，不误报
  2. Checker 对 <select> 元素：value 或选中项文本任一匹配即通过
  3. FormFiller._fill_field：select 走 select_option，input 走 fill
  4. main_window._on_source_extracted 喂入属性池的是「品种属性名」
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.product import Product
from core.property_pool import PropertyPool
from page.filler import FormFiller
from verifier.checker import Checker


# ------------------------------------------------------------------
# 测试用假对象：模拟 Playwright 的 page / locator
# ------------------------------------------------------------------
class FakeElement:
    def __init__(self, tag: str, value: str = "", text: str = ""):
        self._tag = tag
        self._value = value
        self._text = text

    def evaluate(self, script: str):
        if "tagName" in script:
            return self._tag
        if "selectedOptions" in script:
            return self._text
        raise AssertionError(f"unexpected evaluate: {script}")

    def input_value(self) -> str:
        return self._value

    def fill(self, value: str):
        self._value = value
        self._tag = "input-filled"

    def select_option(self, value=None, label=None):
        # 模拟：value 匹配成功；否则 label 匹配；都不匹配则抛异常
        if value is not None:
            if value == self._value:
                return
            raise RuntimeError("option not found by value")
        if label is not None:
            if label == self._text:
                self._value = label  # 选中后 value 变成 label（模拟按文本选中）
                return
            raise RuntimeError("option not found by label")


class FakePage:
    """locator -> FakeElement 的注册表。"""

    def __init__(self, elements: dict):
        self._elements = elements  # {locator: FakeElement}

    def locator(self, sel: str):
        return self._elements[sel]

    def input_value(self, sel: str):
        return self._elements[sel].input_value()


def _build_product():
    return Product.from_dict({
        "name": "回归测试品种",
        "target_url": "https://example.com",
        "field_mapping": [
            {"property": "学生姓名", "locator": 'input[name="xm"]'},
            {"property": "保险方案", "locator": 'select[name="baoxian"]'},
        ],
    })


# ------------------------------------------------------------------
# 1. 空值跳过
# ------------------------------------------------------------------
def test_blank_expected_skipped():
    product = _build_product()
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "", "保险方案": None})  # 空串 + None 都不该校验

    page = FakePage({
        'input[name="xm"]': FakeElement("input", value="张三"),
        'select[name="baoxian"]': FakeElement("select", value="basic"),
    })
    issues = Checker().verify(page, product, pool)
    assert issues == [], f"空值不应产生校验问题，实际: {issues}"

    # snapshot 版本同样验证
    issues2 = Checker().verify_snapshot(
        {'input[name="xm"]': "张三"}, product, pool)
    assert issues2 == [], f"verify_snapshot 空值不应报问题，实际: {issues2}"
    print("✓ 空值（None/''）跳过校验")


# ------------------------------------------------------------------
# 2. select 元素校验：value 或文本任一匹配即通过
# ------------------------------------------------------------------
def test_select_verify_by_value_or_text():
    product = _build_product()
    pool = PropertyPool()
    pool.feed_dict({"保险方案": "basic_50"})  # 期望值 = option 的 value

    # 页面 select 的 value=basic_50，文本="基础版50元" → 按 value 匹配，通过
    page = FakePage({
        'input[name="xm"]': FakeElement("input", value="张三"),
        'select[name="baoxian"]': FakeElement(
            "select", value="basic_50", text="基础版50元"),
    })
    issues = Checker().verify(page, product, pool)
    assert issues == [], f"value 匹配时不应报问题，实际: {issues}"

    # 期望值是可见文本"基础版50元" → 按文本匹配，也应通过（修复前的误报场景）
    pool2 = PropertyPool()
    pool2.feed_dict({"保险方案": "基础版50元"})
    issues2 = Checker().verify(page, product, pool2)
    assert issues2 == [], f"文本匹配时不应报问题，实际: {issues2}"

    # 期望值完全对不上 → 必须报问题
    pool3 = PropertyPool()
    pool3.feed_dict({"保险方案": "豪华版200元"})
    issues3 = Checker().verify(page, product, pool3)
    assert len(issues3) == 1 and issues3[0]["property"] == "保险方案"
    print("✓ select 校验：value/文本任一匹配通过，都不匹配才报")


# ------------------------------------------------------------------
# 3. _fill_field：select 用 select_option，input 用 fill
# ------------------------------------------------------------------
def test_fill_field_select_and_input():
    input_el = FakeElement("input", value="")
    select_el = FakeElement("select", value="basic_50", text="基础版50元")
    page = FakePage({
        'input[name="xm"]': input_el,
        'select[name="baoxian"]': select_el,
    })

    # input 走 fill
    FormFiller._fill_field(page, 'input[name="xm"]', "张三")
    assert input_el.input_value() == "张三"

    # select 按 value 匹配成功
    FormFiller._fill_field(page, 'select[name="baoxian"]', "basic_50")
    assert select_el.input_value() == "basic_50"

    # select 按 value 匹配失败 → 兜底按文本匹配
    FormFiller._fill_field(page, 'select[name="baoxian"]', "基础版50元")
    assert select_el.input_value() == "基础版50元"

    # 两者都失败 → 抛异常（由外层捕获记为 error）
    try:
        FormFiller._fill_field(page, 'select[name="baoxian"]', "不存在的选项")
        raise AssertionError("应该抛异常")
    except RuntimeError:
        pass
    print("✓ _fill_field：input→fill，select→select_option（value 优先/label 兜底）")


# ------------------------------------------------------------------
# 4. _on_source_extracted 喂入的是品种属性名
# ------------------------------------------------------------------
def test_source_extracted_feeds_product_property_names():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    from gui.main_window import MainWindow
    from sources.idcard_ocr import IdCardOCR

    w = MainWindow()
    product = _build_product()
    w._on_product_selected(product)

    # 模拟拖图：身份证 OCR 返回的原始 key 是 "姓名"（非品种属性名 "学生姓名"）
    group = w.property_panel.source_groups.get("身份证OCR")
    assert group is not None, "身份证OCR 分组应存在"
    group.set_values({"姓名": "张三", "_source": "mock"})
    # 此时输入框已回填，触发主窗口的喂池逻辑
    w._on_source_extracted("身份证OCR", {"姓名": "张三", "_source": "mock"})

    pool_values = w.page_panel._pool.all()
    assert "学生姓名" in pool_values, f"池里应有品种属性名，实际: {pool_values}"
    assert pool_values["学生姓名"] == "张三"
    assert "姓名" not in pool_values, f"原始 key 不应进入属性池，实际: {pool_values}"
    print("✓ 识别结果按品种属性名喂池，原始 key 不再污染")


if __name__ == "__main__":
    test_blank_expected_skipped()
    test_select_verify_by_value_or_text()
    test_fill_field_select_and_input()
    test_source_extracted_feeds_product_property_names()
    print()
    print("test_bugfixes.py 全部通过 ✓")
