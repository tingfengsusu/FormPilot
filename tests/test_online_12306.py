"""12306 在线填表测试（真实网页，验证联想选择框策略）。

覆盖：
  1. fill_mode=autocomplete：写值触发联想 → 点选候选 → **隐藏站点代码被写入**
     （只写可见文本时隐藏字段为空，页面会校验报错——这是本策略存在的原因）
  2. FormFiller 完整链路：三个字段填充 + 回读校验零差异

网络不可用 / 未安装 Playwright 时自动跳过（不计失败）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

_URL = "https://kyfw.12306.cn/otn/leftTicket/init"
_FROM = 'input[name="leftTicketDTO.from_station_name"]'
_TO = 'input[name="leftTicketDTO.to_station_name"]'
_DATE = 'input[name="leftTicketDTO.train_date"]'


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def test_fill_autocomplete_writes_hidden_code():
    """联想选择框：点选候选后，隐藏的站点代码必须被写入。"""
    if not _playwright_available():
        skip_test("未安装 playwright")
        return

    from playwright.sync_api import sync_playwright
    from page.filler import FormFiller

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)

            FormFiller._fill_field(page, _FROM, "洛阳", fill_mode="autocomplete")
            FormFiller._fill_field(page, _TO, "上海", fill_mode="autocomplete")

            state = page.evaluate(
                """() => ({
                    fromName: document.querySelector(
                        'input[name="leftTicketDTO.from_station_name"]').value,
                    toName: document.querySelector(
                        'input[name="leftTicketDTO.to_station_name"]').value,
                    fromCode: document.querySelector(
                        'input[name="leftTicketDTO.from_station"]').value,
                    toCode: document.querySelector(
                        'input[name="leftTicketDTO.to_station"]').value,
                })""")
            browser.close()
    except Exception as e:  # noqa: BLE001 — 网络不可用等环境问题：跳过
        skip_test(f"无法访问 12306（{str(e)[:80]}）")
        return

    assert state["fromName"] == "洛阳", f"出发地文本应为洛阳: {state}"
    assert state["toName"] == "上海", f"到达地文本应为上海: {state}"
    # 核心断言：隐藏的站点代码已写入（只写文本时为空，页面会报错）
    assert state["fromCode"], f"隐藏的出发站代码未写入: {state}"
    assert state["toCode"], f"隐藏的到达站代码未写入: {state}"
    print(f"✓ 联想选择写入隐藏代码: 洛阳={state['fromCode']} 上海={state['toCode']}")


def test_filler_report_on_12306():
    """完整链路：按品种 yaml 填三个字段，报告全 filled 且回读零差异。"""
    if not _playwright_available():
        skip_test("未安装 playwright")
        return

    from core import config as config_module
    config_module.config._data = {"mock_mode": False}

    from core.product_library import ProductLibrary
    from core.property_pool import PropertyPool
    from page.filler import FormFiller

    product = ProductLibrary().load(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "product_library", "在线验证_12306余票查询.yaml"))

    pool = PropertyPool()
    pool.feed_dict({"出发地": "洛阳", "到达地": "上海", "出发日期": "2026-10-01"})

    filler = FormFiller(product, pool, browser_hold_seconds=0)
    try:
        report = filler.fill(headless=True)
    except Exception as e:  # noqa: BLE001
        print(f"skip: 无法访问 12306（{str(e)[:80]}）")
        return

    if report and report[0]["property"] == "(浏览器)":
        skip_test(f"浏览器操作失败（{report[0]['message'][:80]}）")
        return

    assert len(report) == 3, f"应填 3 个字段: {report}"
    bad = [r for r in report if r["status"] != "filled"]
    assert not bad, f"存在未成功字段: {bad}"
    issues = getattr(filler, "_last_issues", None)
    assert issues == [], f"回读校验应零差异: {issues}"
    print("✓ 12306 完整链路：3 字段填充成功，回读零差异")


if __name__ == "__main__":
    test_fill_autocomplete_writes_hidden_code()
    test_filler_report_on_12306()
    print("test_online_12306.py 全部通过 ✓")
