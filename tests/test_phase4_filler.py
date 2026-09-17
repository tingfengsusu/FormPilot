"""Phase 4 自动填表 + 回读校验测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.product import Product
from core.property_pool import PropertyPool
from page.filler import FormFiller
from verifier.checker import Checker


def _build_product():
    return Product.from_dict({
        "name": "测试品种",
        "target_url": "https://example.com",
        "field_mapping": [
            {"property": "学生姓名", "locator": 'input[name="xm"]'},
            {"property": "学生身份证号", "locator": 'input[name="sfzh"]'},
            {"property": "监护人电话", "locator": 'input[name="lxdh"]'},
        ],
    })


def test_filler_mock_fill():
    """Mock 填表：做值可用性检查（直接测 _mock_fill，不依赖全局配置）。"""
    product = _build_product()
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "张三", "学生身份证号": "410300201509011234"})

    filler = FormFiller(product, pool)
    report = filler._mock_fill()

    statuses = {r["property"]: r["status"] for r in report}
    assert statuses["学生姓名"] == "filled"
    assert statuses["学生身份证号"] == "filled"
    assert statuses["监护人电话"] == "missing"  # 必填但为空


def test_checker_verify_snapshot_pass():
    """回读校验：值一致时不报问题。"""
    product = _build_product()
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "张三", "学生身份证号": "410300201509011234"})

    page_values = {
        'input[name="xm"]': "张三",
        'input[name="sfzh"]': "410300201509011234",
    }
    issues = Checker().verify_snapshot(page_values, product, pool)
    assert issues == []


def test_checker_verify_snapshot_mismatch():
    """回读校验：值不一致时报告。"""
    product = _build_product()
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "张三", "学生身份证号": "410300201509011234"})

    page_values = {
        'input[name="xm"]': "张三",
        'input[name="sfzh"]': "410300201509019999",  # 故意写错
    }
    issues = Checker().verify_snapshot(page_values, product, pool)
    assert len(issues) == 1
    assert issues[0]["property"] == "学生身份证号"
    assert issues[0]["expected"] == "410300201509011234"
    assert issues[0]["actual"] == "410300201509019999"


def test_checker_verify_respects_verify_flag():
    """回读校验：verify_after_fill=False 的字段应跳过。"""
    product = Product.from_dict({
        "name": "测试",
        "field_mapping": [
            {"property": "学生姓名", "locator": 'input[name="xm"]', "verify_after_fill": False},
            {"property": "学生身份证号", "locator": 'input[name="sfzh"]', "verify_after_fill": True},
        ],
    })
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "张三", "学生身份证号": "abc"})
    page_values = {
        'input[name="xm"]': "李四",  # 不该校验（verify=False）
        'input[name="sfzh"]': "abc",
    }
    issues = Checker().verify_snapshot(page_values, product, pool)
    # 只有 sfzh 会校验，且一致，所以无问题
    assert issues == []


if __name__ == "__main__":
    test_filler_mock_fill()
    test_checker_verify_snapshot_pass()
    test_checker_verify_snapshot_mismatch()
    test_checker_verify_respects_verify_flag()
    print("test_phase4_filler.py 全部通过 ✓")