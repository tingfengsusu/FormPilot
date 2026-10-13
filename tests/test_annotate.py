"""页面内批注层测试（设计见《重构设计》§十四）。

覆盖：
  - 纯逻辑：期望值/一致性判定（属性池为空 → 跳过，与回读校验同口径）
  - 真浏览器：贴批注 → 浮层存在、条数正确 → 清理 → 浮层消失
  - 提交守卫：``FillSession.submit_now()`` 会先清掉批注层
  - 找不到元素时不注入（返回 0）

页面用本地 ``tests/mock_form.html``（file://），不访问外部系统；
浏览器不可用时自动跳过。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import browser_available, skip_test

from core.product import Product
from core.property_pool import PropertyPool
from engine import FillEngine
from engine.annotate import (ANNOTATION_ID, annotate_expected, annotate_page,
                             build_annotations, clear_annotations,
                             is_annotated)

_MOCK_PAGE = "file:///" + os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "mock_form.html").replace("\\", "/")


def _product() -> Product:
    return Product.from_dict({
        "name": "批注测试",
        "target_url": _MOCK_PAGE,
        "verify_delay_ms": 50,
        "field_mapping": [
            {"property": "学生姓名", "locator": 'input[name="xm"]'},
            {"property": "学生身份证号", "locator": 'input[name="sfzh"]'},
            {"property": "监护人姓名", "locator": 'input[name="jhrmc"]',
             "required": False},
        ],
    })


def _pool() -> PropertyPool:
    pool = PropertyPool()
    pool.feed_dict({"学生姓名": "张三", "学生身份证号": "410305201509011230"})
    return pool


# ----------------------------------------------------------------------
# 纯逻辑（无浏览器）
# ----------------------------------------------------------------------
def test_build_annotations_marks_match_and_diff():
    issues = [{"property": "学生身份证号", "expected": "410305201509011230",
               "actual": "410305201509011231", "locator": 'input[name="sfzh"]'}]
    entries = build_annotations(_product(), _pool(), issues)
    by_prop = {e["property"]: e for e in entries}

    assert by_prop["学生姓名"]["kind"] == "ok"
    assert by_prop["学生姓名"]["text"] == "张三"
    assert by_prop["学生身份证号"]["kind"] == "diff"
    assert "期望" in by_prop["学生身份证号"]["text"]
    assert by_prop["学生身份证号"]["actual"] == "410305201509011231"
    # 属性池里没有的字段（监护人姓名）不贴批注 —— 与回读校验同口径
    assert "监护人姓名" not in by_prop


def test_build_annotations_without_issues_is_all_match():
    entries = build_annotations(_product(), _pool())
    assert entries and all(e["kind"] == "ok" for e in entries)


def test_annotate_page_returns_zero_when_nothing_found():
    class _FakePage:
        def locator(self, _locator):
            raise RuntimeError("元素不存在")

        def evaluate(self, *_args):        # 不该被调用
            raise AssertionError("没有可贴的字段时不应注入浮层")

    assert annotate_page(_FakePage(), [{"locator": "input#nope",
                                        "kind": "ok", "text": "x"}]) == 0


# ----------------------------------------------------------------------
# 真浏览器
# ----------------------------------------------------------------------
def test_annotate_and_clear_on_real_page():
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    product = _product()
    engine = FillEngine(product, headless=True)
    with engine.session() as session:
        session.fill(_pool())
        page = session.page

        placed = annotate_expected(page, product, _pool())
        assert placed == 2                       # 两个有值的字段
        assert is_annotated(page) is True
        assert page.evaluate(
            f"() => document.querySelectorAll('#{ANNOTATION_ID} > div').length") >= 2

        clear_annotations(page)
        assert is_annotated(page) is False


def test_submit_clears_annotations_first():
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    product = _product()
    engine = FillEngine(product, headless=True)
    with engine.session() as session:
        session.fill(_pool())
        page = session.page
        annotate_expected(page, product, _pool())
        assert is_annotated(page) is True

        session.submit_now()                     # 提交前必须先清掉批注层
        assert is_annotated(page) is False


if __name__ == "__main__":
    test_build_annotations_marks_match_and_diff()
    test_build_annotations_without_issues_is_all_match()
    test_annotate_page_returns_zero_when_nothing_found()
    test_annotate_and_clear_on_real_page()
    test_submit_clears_annotations_first()
    print("test_annotate.py 全部通过 ✓")
