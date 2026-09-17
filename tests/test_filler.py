"""填表器测试（骨架阶段用 Mock）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.product import Product
from core.property_pool import PropertyPool
from page.filler import FormFiller


def _build_product():
    return Product.from_dict({
        "name": "测试品种",
        "target_url": "https://example.com",
        "field_mapping": [
            {"property": "学生姓名", "locator": 'input[name="xm"]'},
            {"property": "学生身份证号", "locator": 'input[name="sfzh"]'},
        ],
    })


def test_filler_missing_required():
    product = _build_product()
    pool = PropertyPool()
    filler = FormFiller(product, pool)
    report = filler._mock_fill()  # 直接测 Mock 逻辑，不依赖全局配置
    statuses = {r["property"]: r["status"] for r in report}
    assert statuses["学生姓名"] == "missing"
    assert statuses["学生身份证号"] == "missing"


def test_filler_filled():
    product = _build_product()
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "张三", "学生身份证号": "410300201509011234"})
    filler = FormFiller(product, pool)
    report = filler._mock_fill()
    statuses = {r["property"]: r["status"] for r in report}
    assert statuses["学生姓名"] == "filled"
    assert statuses["学生身份证号"] == "filled"


def test_fill_mode_roundtrip():
    """fill_mode 序列化：autocomplete 写入 yaml，plain 不写（保持配置干净）。"""
    from core.product import FieldMapping

    m = FieldMapping.from_dict({"property": "出发地", "locator": "#a",
                                "fill_mode": "autocomplete"})
    assert m.fill_mode == "autocomplete"
    d = Product(name="t", field_mapping=[m]).to_dict()
    assert d["field_mapping"][0]["fill_mode"] == "autocomplete"

    m2 = FieldMapping.from_dict({"property": "x", "locator": "#b"})
    assert m2.fill_mode == "plain"
    d2 = Product(name="t2", field_mapping=[m2]).to_dict()
    assert "fill_mode" not in d2["field_mapping"][0]
    assert FieldMapping.from_dict(d2["field_mapping"][0]).fill_mode == "plain"


def test_fill_field_dispatches_autocomplete_mode():
    """_fill_field 按 fill_mode 分派到联想选择实现。"""
    import page.filler as filler_mod

    calls = {}
    old = filler_mod.FormFiller._fill_autocomplete
    filler_mod.FormFiller._fill_autocomplete = staticmethod(
        lambda page, locator, value: calls.update(locator=locator, value=value))
    try:
        filler_mod.FormFiller._fill_field(
            None, 'input[name="from"]', "洛阳", fill_mode="autocomplete")
        assert calls == {"locator": 'input[name="from"]', "value": "洛阳"}
    finally:
        filler_mod.FormFiller._fill_autocomplete = old


if __name__ == "__main__":
    test_filler_missing_required()
    test_filler_filled()
    test_fill_mode_roundtrip()
    test_fill_field_dispatches_autocomplete_mode()
    print("test_filler.py 全部通过 ✓")