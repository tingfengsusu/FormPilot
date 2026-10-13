"""界面导航（业务 → 操作）与「载入数据」（表格一条记录 → 属性面板）。

写法与 tools/smoke_*.py 一致：**一个 QApplication、顺序断言**（离屏跑）。
（Qt 在极少数情况下会静默 abort；逐用例各自建 app 的写法在这台机器上会触发，
所以 GUI 检查统一用这种"一次成型"的风格。）

运行：QT_QPA_PLATFORM=offscreen python tests/test_gui_nav.py
"""
from __future__ import annotations

import csv
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton

_APP = QApplication.instance() or QApplication(sys.argv)

from gui.business_page import BusinessPage
from gui.main_window import MainWindow
from gui.theme import apply_theme

apply_theme(_APP)


def test_business_page_offers_two_actions():
    page = BusinessPage()
    texts = [b.text() for b in page.findChildren(QPushButton)]

    assert any("批量录入" in t for t in texts), texts
    assert any("单条录入" in t for t in texts), texts


def test_nav_is_business_then_batch():
    """导航层级：保险（业务）→ 批量录入（操作）→ 页面，且都能返回。"""
    window = MainWindow()

    assert window.business_page is None and window.batch_page is None

    window.business_action.trigger()                  # 工具栏「保险」
    assert window.business_page is not None
    assert window.stack.currentWidget() is window.business_page

    window._open_batch_page()                         # 业务页里的「批量录入」
    assert window.stack.currentWidget() is window.batch_page

    window.batch_page.back_requested.emit()           # 批量页「‹ 返回保险」
    assert window.stack.currentWidget() is window.business_page


def test_load_record_from_file_maps_columns_to_properties():
    """载入数据：表头（可能是别名）→ 列名字典 → 品种属性名。"""
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "one.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["被保人姓名", "被保人身份证", "监护人电话",
                             "学校", "年级", "班级", "保险期限", "备注"])
            writer.writerow(["张三", "410305201509011230", "13900000000",
                             "柳泉小学", "三年级", "三年级二班", "8.22-8.21", "x"])

        values = MainWindow._record_from_file(path)

    assert values["学生姓名"] == "张三"
    assert values["学生身份证号"] == "410305201509011230"
    assert values["监护人电话"] == "13900000000"
    assert values["学校名称"] == "柳泉小学"
    assert values["年级"] == "三年级"
    assert values["班级"] == "三年级二班"
    # 整列文本形态的「保险期限」没有对应的品种属性（配方里是拆开的起/止日期）
    # —— 认不出就跳过，不猜
    assert "保险期间起" not in values


def test_batch_page_has_product_selector():
    """批量页要能看见/选择「用哪张页面录」——以前这个值只在配方 yaml 里写死。"""
    from _helpers import use_temp_key

    from batch.db import BatchDB
    from batch.recipe import Recipe
    from gui.batch_page import BatchPage

    with tempfile.TemporaryDirectory() as td:
        use_temp_key(td)
        db = BatchDB(os.path.join(td, "t.db"))
        try:
            recipe = Recipe.load()
            page = BatchPage(db=db, recipe=recipe)

            # 选择器里至少包含配方默认品种，且默认选中它
            default_product = recipe.execute.get("product", "")
            assert default_product, "配方里应有默认 product"
            names = [page.product_combo.itemText(i)
                     for i in range(page.product_combo.count())]
            assert default_product in names, names
            assert page.product_combo.currentText() == default_product
            # 提示里给出目标 URL（选错品种 = 填错系统）
            hint = page.product_hint.text()
            assert hint == "" or hint.startswith("→ http"), hint
            # 运行用的是界面上选的品种（副本，不动原配方）
            page.product_combo.setCurrentText(default_product)
            assert page._runner_recipe().execute["product"] == default_product
            assert recipe.execute["product"] == default_product
        finally:
            db.close()          # 不关连接的话临时目录删不掉（Windows 会占用）


def test_product_page_edit_roundtrip():
    """品种编辑页（单页，取代向导）：载入 → 改 → 收集一致 → 保存到临时库。"""
    from PyQt5.QtCore import Qt

    from core.product import Product
    from core.product_library import ProductLibrary
    from core.property_def import PropertyDefLibrary
    from gui.product_page import ProductPage

    with tempfile.TemporaryDirectory() as td:
        library = ProductLibrary(library_dir=td)
        page = ProductPage(library=library,
                           prop_def_library=PropertyDefLibrary())
        product = Product.from_dict({
            "name": "测试品种", "description": "描述",
            "target_url": "https://example.com/f",
            "context": {"mobile": True, "wechat": True,
                        "referer": "https://example.com"},
            "field_mapping": [
                {"property": "学生姓名", "locator": 'input[name="xm"]'},
                {"property": "年级", "locator": "div.grade",
                 "verify_after_fill": False, "fill_mode": "select"},
            ],
        })

        page.load_product(product)
        assert page.name_edit.text() == "测试品种"
        assert page.table.rowCount() == 2
        assert page.mobile_check.isChecked() and page.wechat_check.isChecked()

        # 改：换 locator + 取消「学生姓名」的回读校验
        page._set_cell_text(0, 1, 'input[name="xm2"]')
        page.table.item(0, 3).setCheckState(Qt.Unchecked)

        back = page.collect_product()
        assert back.name == "测试品种" and back.description == "描述"
        assert back.target_url == "https://example.com/f"
        assert back.context["mobile"] is True
        assert back.context["referer"] == "https://example.com"
        assert back.field_mapping[0].locator == 'input[name="xm2"]'
        assert back.field_mapping[0].verify_after_fill is False
        assert back.field_mapping[1].fill_mode == "select"
        assert back.field_mapping[1].verify_after_fill is False

        # 保存到临时库（不碰仓库里的 config/product_library）
        assert page.save() is True
        saved_path = os.path.join(td, "测试品种.yaml")
        assert os.path.exists(saved_path)
        assert library.load(saved_path).field_mapping[0].verify_after_fill is False

        # 新建模式：清空
        page.load_product(None)
        assert page.table.rowCount() == 0 and page.name_edit.text() == ""


def main() -> int:
    test_business_page_offers_two_actions()
    print("  · 业务页两张操作卡 ✓")
    test_nav_is_business_then_batch()
    print("  · 导航：保险 → 批量录入 → 返回 ✓")
    test_batch_page_has_product_selector()
    print("  · 批量页品种选择器（默认取配方、运行用所选）✓")
    test_product_page_edit_roundtrip()
    print("  · 品种编辑页：载入 → 改 → 保存 ✓")
    test_load_record_from_file_maps_columns_to_properties()
    print("  · 载入数据：列名字典 → 品种属性名 ✓")
    print("test_gui_nav.py 全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
