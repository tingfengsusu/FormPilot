"""自测截图：把界面与批注效果渲染成 PNG —— 不需要真实显示器，也不需要用户当第一双眼睛。

用法::

    python tools/selfcheck_screens.py            # 只截界面
    python tools/selfcheck_screens.py --browser  # 额外跑真浏览器（本地 mock 页）+ 报告页

产出在 ``data/selfcheck/``：

    01_main.png       主界面（品种库 + 属性面板 + 填表报告）
    02_batch.png      批量录入页（品种选择器 + 面包屑）
    03_product.png    品种编辑页（单页表单）
    05_annotate.png   真浏览器：填完表后的**页面内批注层**（期望值/红框）
    06_report.png     归档对比报告（HTML 渲染）

实现要点（踩过的坑）：

- **不能用 ``QT_QPA_PLATFORM=offscreen``**：这台机器上 offscreen/minimal 都渲染不出字形，
  截出来只有布局没有文字。改成**真实平台 + 不调 ``show()``**：不开窗口、不打扰用户，
  但字体正常（grab() 不需要窗口可见）
- 页面放进 QStackedWidget 后要手动 resize + 激活布局，否则 grab 出来是空的
- 想看**真实运行中窗口**（含 DPI、真实字体）用 ``tools/selfcheck_window.ps1``（PrintWindow 抓屏）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "selfcheck")

#: 页面截图尺寸（够看清布局与文字，又不至于太大）
_PAGE_SIZE = (1264, 700)


def _save(widget, name: str, size=None) -> str:
    from PyQt5.QtWidgets import QApplication

    if size:
        widget.resize(*size)
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    QApplication.processEvents()
    path = os.path.join(_OUT_DIR, name)
    ok = widget.grab().save(path, "PNG")
    print(("  ✓ " if ok else "  ✗ ") + path)
    return path


def shoot_ui() -> None:
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from gui.theme import apply_theme

    apply_theme(app)

    from gui.main_window import MainWindow

    window = MainWindow()
    # 不 show()：grab() 不需要窗口可见，这样既不打扰用户又能拿到正常字体
    _save(window, "01_main.png", (1280, 800))

    window.batch_btn.click()                     # 批量录入（工具栏，用当前选中的品种）
    _save(window.batch_page, "02_batch.png", _PAGE_SIZE)

    product = None
    for item in window.product_list.products.values():
        if "基础" in item.name:           # 挑一个字段多的品种，好看清表格
            product = item
            break
    if product is None:
        product = next(iter(window.product_list.products.values()), None)
    window._open_product_page(product)
    _save(window.product_page, "03_product.png", _PAGE_SIZE)


def shoot_browser() -> None:
    """真浏览器 + 本地 mock 页：截批注层，并渲染对比报告。"""
    from core.config import config as _cfg

    _cfg._data = {"mock_mode": False}          # 真开浏览器，但页面是本地文件

    from core.product import Product
    from core.product_library import ProductLibrary
    from core.property_pool import PropertyPool
    from engine import FillEngine

    product = None
    for item in ProductLibrary().list_products():
        if item.name == "学平险_本地Mock测试":
            product = item
            break
    if product is None:
        print("  ! 找不到本地 mock 品种，跳过浏览器截图")
        return

    pool = PropertyPool()
    pool.feed_dict({
        "学生姓名": "张小明", "学生身份证号": "410305201509011230",
        "监护人姓名": "张大山", "监护人电话": "13900000000",
        "保险方案": "基础版（100元）",
    })
    # 造一条「回读不一致」看红框效果：把页面上的监护人电话改掉再回读
    engine = FillEngine(product, headless=True)
    with engine.session() as session:
        session.fill(pool)
        page = session.page
        page.fill('input[name="lxdh"]', "13800000001")      # 制造不一致
        issues = [{"property": "监护人电话", "expected": "13900000000",
                   "actual": "13800000001", "locator": 'input[name="lxdh"]'}]
        placed = session.annotate(pool, issues=issues)
        print(f"  · 批注贴了 {placed} 条")
        out = os.path.join(_OUT_DIR, "05_annotate.png")
        page.screenshot(path=out, full_page=True)
        print("  ✓ " + out)

    # 报告页：用真实构件生成一份带差异的 HTML，再用浏览器渲染截图
    from engine.report_html import records_from_results, render_report, write_report

    class _Result:
        ok = False
        attempts = 1
        screenshot = ""
        problems = ["[监护人电话] 回读不一致: 期望 13900000000 实际 13800000001"]
        report = [
            {"property": "学生姓名", "status": "filled", "value": "张小明"},
            {"property": "学生身份证号", "status": "filled",
             "value": "410305201509011230"},
            {"property": "监护人电话", "status": "mismatch", "value": "13900000000",
             "expected": "13900000000", "actual": "13800000001"},
        ]

    html_path = os.path.join(_OUT_DIR, "sample_report.html")
    write_report(html_path, render_report("自测样例报告", records_from_results(
        [("张小明", _Result())], product=product)))

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 900, "height": 620})
        page.goto("file:///" + html_path.replace("\\", "/"))
        page.wait_for_timeout(300)
        out = os.path.join(_OUT_DIR, "06_report.png")
        page.screenshot(path=out, full_page=True)
        print("  ✓ " + out)
        browser.close()


def main() -> int:
    os.makedirs(_OUT_DIR, exist_ok=True)
    print("界面截图（离屏）：")
    shoot_ui()
    if "--browser" in sys.argv:
        print("浏览器截图（本地 mock 页）：")
        shoot_browser()
    print(f"\n产出目录：{_OUT_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
