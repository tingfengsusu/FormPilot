"""生成 README 演示截图（真实窗口渲染，不造假数据）。

产物写入 docs/screenshots/，用于 README 演示区。
批量页优先选中「待复核最多、异常最少」的真实批次，避免截图里全是失败行。

运行：python tools/shoot_screenshots.py
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.theme import apply_theme

OUT_DIR = os.path.join(ROOT, "docs", "screenshots")
DB_PATH = os.path.join(ROOT, "data", "batch.db")
HERO_PRODUCT = "太保洛阳学平险_基础"


def pump(app: QApplication, ms: int = 400) -> None:
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def shot(win, name: str) -> None:
    path = os.path.join(OUT_DIR, name)
    win.grab().save(path)
    print("已保存", os.path.relpath(path, ROOT))


def select_product(win, name: str) -> None:
    lw = win.product_list.list_widget
    for i in range(lw.count()):
        if lw.item(i).data(Qt.UserRole) == name:
            lw.setCurrentRow(i)
            return


def best_batch_id():
    """真实批次里挑一个适合展示的：待复核最多、异常/失败最少。"""
    if not os.path.exists(DB_PATH):
        return None
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            """select b.id,
                      (select count(*) from applicant a
                        where a.batch_id=b.id and a.status='待复核') rev,
                      (select count(*) from applicant a
                        where a.batch_id=b.id and a.status in ('录入失败','数据异常')) bad
                 from batch b""").fetchall()
    finally:
        con.close()
    rows = [r for r in rows if r[1] > 0]
    if not rows:
        return None
    rows.sort(key=lambda r: (-r[1], r[2]))
    return rows[0][0]


def fill_property_panel(win) -> None:
    """用 Mock 数据把属性面板填满，截图里能看到真实的分组与字段。"""
    from core import mock_data

    values = {
        "身份证OCR": mock_data.idcard_mock(),
        "户口本OCR": mock_data.household_mock(),
        "Excel名单": mock_data.excel_mock(),
        "保险员信息": {"保险员姓名": "李经理", "保险员工号": "TPY0001",
                       "保险员电话": "13900000000"},
        "其他": {"被保人手机号": "13800000000", "保险方案": "加强版（150元）",
                 "学生类别": "小学生", "年级": "三年级",
                 "保险期间起": "2026-10-29", "保险期间止": "2027-10-28"},
    }
    for source, group in win.property_panel.source_groups.items():
        data = values.get(source)
        if data:
            group.set_values(data)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    app = QApplication(sys.argv)
    apply_theme(app)

    win = MainWindow()
    win.resize(1280, 900)
    win.show()
    pump(app, 900)

    # 1) 主界面：选一个字段最全的品种，用 Mock 数据填满属性面板
    select_product(win, HERO_PRODUCT)
    pump(app, 800)
    product = win.page_panel._product
    if product is not None:
        # 公开仓库里配置存的是占位参数；截图用干净的入口地址
        product.target_url = "https://dwx.cpic.com.cn/cxNew/dwx/mobilemsbv2/"
        win.page_panel.set_product(product)
        win.url_input.setText(product.target_url)
        pump(app, 300)
    fill_property_panel(win)
    pump(app, 700)
    # 滚到属性分组区，截图里能直接看到已填的值
    win.property_panel.scroll.verticalScrollBar().setValue(300)
    pump(app, 400)
    shot(win, "01-main-window.png")

    # 2) 品种编辑页（单页表单，取代原 5 步向导）
    if product is not None:
        win._open_product_page(product)
        pump(app, 800)
        shot(win, "02-product-editor.png")
        win._show_main_page()
        pump(app, 300)

    # 3) 批量录入页：选中待复核最多的真实批次
    win._on_open_batch_from_workbench()
    pump(app, 900)
    target = best_batch_id()
    page = win.batch_page
    if target is not None and page is not None:
        for i in range(page.batch_list.count()):
            if page.batch_list.item(i).data(Qt.UserRole) == target:
                page.batch_list.setCurrentRow(i)
                break
        pump(app, 700)
    shot(win, "03-batch-review.png")

    print("完成：docs/screenshots/01_主界面.png、02_品种编辑.png、03_批量录入.png")


if __name__ == "__main__":
    main()
