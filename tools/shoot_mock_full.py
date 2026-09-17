"""生成 Mock 页面演示截图（空状态 + 已填状态）。

产物写入 docs/screenshots/，用于 README 演示区。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = "file:///" + os.path.join(ROOT, "tests", "mock_form_full.html").replace("\\", "/")
OUT_DIR = os.path.join(ROOT, "docs", "screenshots")

MOCK_VALUES = {
    "xm": "测试学生", "xb": "男", "csrq": "2015-09-01", "mz": "汉",
    "sfzh": "410300201509011234", "dz": "河南省洛阳市测试路 1 号",
    "jhrmc": "王大力", "yhzgx": "父子", "hzxm": "王大力", "lxdh": "13800000000",
    "xx": "洛阳市实验小学", "bj": "三年级二班", "baoxian": "enhanced",
    "bxyxm": "李经理", "bxygh": "TPY0001", "bxydh": "13900000000",
}


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 480, "height": 900})
        page.goto(PAGE)
        page.wait_for_load_state("networkidle")

        page.screenshot(path=os.path.join(OUT_DIR, "mock_form_full_empty.png"),
                        full_page=True)

        for name, value in MOCK_VALUES.items():
            el = page.locator(f'[name="{name}"]')
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "select":
                el.select_option(value)
            else:
                el.fill(value)

        page.click('button[type="submit"]')
        page.wait_for_timeout(200)
        page.screenshot(path=os.path.join(OUT_DIR, "mock_form_full_filled.png"),
                        full_page=True)
        browser.close()
    print("截图已生成：docs/screenshots/mock_form_full_empty.png / mock_form_full_filled.png")


if __name__ == "__main__":
    main()
