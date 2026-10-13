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


def main() -> int:
    test_business_page_offers_two_actions()
    print("  · 业务页两张操作卡 ✓")
    test_nav_is_business_then_batch()
    print("  · 导航：保险 → 批量录入 → 返回 ✓")
    test_load_record_from_file_maps_columns_to_properties()
    print("  · 载入数据：列名字典 → 品种属性名 ✓")
    print("test_gui_nav.py 全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
