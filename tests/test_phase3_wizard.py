"""Phase 3 品种创建器测试：扫描、匹配、构建、保存。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page.scanner import Scanner
from page.matcher import FieldMatcher
from core.product import Product, FieldMapping
from core.product_library import ProductLibrary
from core import config as config_module


def _force_mock_mode():
    """扫描/匹配测试针对 Mock 行为编写，显式固定 mock_mode，
    避免受 settings.yaml 当前状态（可能为 false）影响。"""
    config_module.config._data = {"mock_mode": True}


def test_scanner_mock_fields():
    _force_mock_mode()
    scanner = Scanner()
    fields = scanner.scan("https://example.com")
    locators = [f["locator"] for f in fields]
    assert 'input[name="xm"]' in locators
    assert 'select[name="baoxian"]' in locators


def test_matcher_heuristic_mapping():
    _force_mock_mode()
    matcher = FieldMatcher()
    fields = Scanner().scan("https://example.com")
    mapping = matcher.match_heuristic(
        fields, ["学生姓名", "学生身份证号", "监护人电话", "保险方案"]
    )
    assert mapping["学生姓名"] == 'input[name="xm"]'
    assert mapping["学生身份证号"] == 'input[name="sfzh"]'
    assert mapping["监护人电话"] == 'input[name="lxdh"]'
    assert mapping["保险方案"] == 'select[name="baoxian"]'


def test_matcher_match_returns_mapping():
    """match() 在 Mock 模式下应走启发式并返回正确映射。"""
    _force_mock_mode()
    matcher = FieldMatcher()
    fields = Scanner().scan("https://example.com")
    mapping = matcher.match(fields, ["学生姓名", "学生身份证号"])
    assert mapping["学生姓名"] == 'input[name="xm"]'
    assert mapping["学生身份证号"] == 'input[name="sfzh"]'


def test_product_to_dict_roundtrip():
    product = Product(
        name="测试品种",
        description="desc",
        target_url="https://example.com",
        field_mapping=[FieldMapping.from_dict({"property": "学生姓名", "locator": 'input[name="xm"]'})],
    )
    d = product.to_dict()
    assert d["name"] == "测试品种"
    assert len(d["field_mapping"]) == 1
    assert d["field_mapping"][0]["locator"] == 'input[name="xm"]'

    # 读回
    loaded = Product.from_dict(d)
    assert loaded.name == "测试品种"
    assert loaded.property_names() == ["学生姓名"]


def test_product_library_save_load():
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_lib_test")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        lib = ProductLibrary(tmp_dir)
        product = Product(
            name="测试/非法:字符",
            target_url="https://example.com",
            field_mapping=[FieldMapping.from_dict({"property": "学生姓名", "locator": 'input[name="xm"]'})],
        )
        path = lib.save(product)
        assert os.path.exists(path)
        # 文件名应被安全化（非法字符替换为 _）
        assert "测试_非法_字符" in path

        loaded = lib.load(path)
        assert loaded.name == product.name
    finally:
        # 清理
        for f in os.listdir(tmp_dir):
            os.remove(os.path.join(tmp_dir, f))
        os.rmdir(tmp_dir)


if __name__ == "__main__":
    test_scanner_mock_fields()
    test_matcher_heuristic_mapping()
    test_matcher_match_returns_mapping()
    test_product_to_dict_roundtrip()
    test_product_library_save_load()
    print("test_phase3_wizard.py 全部通过 ✓")