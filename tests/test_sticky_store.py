"""「沿用上次的值」：存储与接线。

覆盖：
  - 只记**勾了沿用**的字段；可清除
  - 存储文件损坏/不存在都不该让界面崩（当成空）
  - 选中品种时，沿用的值进属性池（主界面「开始录入」用的就是池里的值）

离屏跑（不开窗口）：QT_QPA_PLATFORM=offscreen。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core.product import Product
from core.property_pool import PropertyPool
from core.sticky_store import StickyStore


def _product() -> Product:
    return Product.from_dict({
        "name": "测试品种", "target_url": "https://example.com",
        "field_mapping": [
            {"property": "手机号", "locator": "#a", "sticky": True},
            {"property": "姓名", "locator": "#b"},
        ],
    })


def test_roundtrip_only_sticky_fields():
    with tempfile.TemporaryDirectory() as td:
        store = StickyStore(path=os.path.join(td, "sticky.json"))
        product = _product()
        pool = PropertyPool()
        pool.feed_dict({"手机号": "13900000000", "姓名": "张三"})

        assert store.sticky_fields(product) == ["手机号"]
        assert store.remember(product, pool) == {"手机号": "13900000000"}
        assert store.prefill(product) == {"手机号": "13900000000"}
        assert "姓名" not in store.prefill(product)      # 没勾沿用的不记

        store.forget("测试品种")
        assert store.prefill(product) == {}


def test_broken_store_file_is_tolerated():
    """存储文件损坏/不存在都不该让界面崩（当成空）。"""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ 这不是 JSON")
        store = StickyStore(path=path)
        assert store.prefill(_product()) == {}
        assert store.remember(_product(), PropertyPool()) == {}


def test_selected_product_feeds_sticky_values_into_pool():
    """选中品种 → 沿用的值进属性池（开始录入时用的就是池里的值）。"""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory() as td:
        os.environ["FORMPILOT_STICKY_PATH"] = os.path.join(td, "s.json")

        from gui.main_window import MainWindow

        window = MainWindow()
        product = next(iter(window.product_list.products.values()))
        mapping = product.field_mapping[0]
        mapping.sticky = True

        pool = PropertyPool()
        pool.feed_dict({mapping.property: "沿用的值"})
        StickyStore().remember(product, pool)

        window._on_product_selected(product)          # 相当于在列表里点了它
        assert window.page_panel._pool.get(mapping.property) == "沿用的值"
        app.processEvents()


if __name__ == "__main__":
    test_roundtrip_only_sticky_fields()
    print("  · 沿用存储：只记勾了的、可清除 ✓")
    test_broken_store_file_is_tolerated()
    print("  · 存储文件损坏时当空处理 ✓")
    test_selected_product_feeds_sticky_values_into_pool()
    print("  · 选中品种带出沿用值（进属性池）✓")
    print("test_sticky_store.py 全部通过 ✓")
