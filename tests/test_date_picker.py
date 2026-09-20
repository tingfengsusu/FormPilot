"""page/date_picker.py 测试（`fill_mode: date`）。

覆盖：
  - ISO / 中文日期解析
  - 原生输入框直接填
  - Vant 风格滚轮：点开 → 年/月/日各点一次 → 确认 → 回读一致
  - 不可选日期（早于今天）时**报错**，不静默错填
  - Filler 集成：`fill_mode: date` 走通整条链
未安装 Playwright / 浏览器不可用时自动跳过。
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

from page.date_picker import DatePickerError, fill_date, parse_iso_date

MOCK_PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "mock_date_picker.html")


def test_parse_iso_date():
    assert parse_iso_date("2026-09-19") == (2026, 9, 19)
    assert parse_iso_date("2026年9月19日") == (2026, 9, 19)
    assert parse_iso_date("2026/09/19") == (2026, 9, 19)
    assert parse_iso_date("2026-02-30") is None      # 不存在的日期
    assert parse_iso_date("") is None
    assert parse_iso_date(None) is None


def _playwright_or_skip():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        skip_test("未安装 Playwright")
        return None, None
    try:
        playwright = sync_playwright().start()
    except Exception as e:  # noqa: BLE001
        skip_test(f"Playwright 不可用：{e}")
        return None, None
    try:
        browser = playwright.chromium.launch(headless=True)
    except Exception as e:  # noqa: BLE001
        playwright.stop()
        skip_test(f"Chromium 未安装：{e}")
        return None, None
    return playwright, browser


def test_fill_date_on_mock_page():
    playwright, browser = _playwright_or_skip()
    if browser is None:
        return
    try:
        url = "file:///" + MOCK_PAGE.replace("\\", "/")
        page = browser.new_page()
        page.goto(url)

        tomorrow = date.today() + timedelta(days=1)
        value = tomorrow.isoformat()

        # 起期：滚轮选择器（点开 → 选年月日 → 确认）
        result = fill_date(page, "#startValue", value)
        assert value in result, result
        assert page.locator("#startValue").inner_text().strip() == value

        # 止期：同一天再填一次（覆盖「选择器已有值」的路径）
        result2 = fill_date(page, "#endValue", value)
        assert value in result2, result2

        # 早于今天的日期不可选 → 必须报错，而不是错填
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        try:
            fill_date(page, "#startValue", yesterday)
            assert False, "早于今天的日期应报错"
        except DatePickerError as e:
            assert "不可选" in str(e) or "不识别" in str(e)
    finally:
        browser.close()
        playwright.stop()


def test_fill_date_corrects_off_by_one_picker():
    """真机怪癖：Vant 滚轮「点 20 日」可能选中 21 日（固定偏移）。

    闭环校验必须把这种偏移**修正回来**，而不是错填或直接失败。
    """
    playwright, browser = _playwright_or_skip()
    if browser is None:
        return
    try:
        url = "file:///" + MOCK_PAGE.replace("\\", "/") + "#offset=1"
        page = browser.new_page()
        page.goto(url)

        tomorrow = date.today() + timedelta(days=1)
        result = fill_date(page, "#startValue", tomorrow.isoformat())
        assert tomorrow.isoformat() in result, result
        assert page.locator("#startValue").inner_text().strip() == tomorrow.isoformat()

        # 修正不过来时必须报错，而不是把错值留下（max_attempts=1 时没有修正机会）
        # 注意挑一个「+1 之后不会因为月末夹紧而恰好等于目标」的日期
        later = (date.today() + timedelta(days=5)).isoformat()
        try:
            fill_date(page, "#endValue", later, max_attempts=1)
            assert False, "偏移未修正时应报错"
        except DatePickerError as e:
            assert "回读不一致" in str(e)
    finally:
        browser.close()
        playwright.stop()


def test_filler_integration():
    """品种声明 fill_mode: date 时，Filler 能直接填（整条链路）。"""
    playwright, browser = _playwright_or_skip()
    if browser is None:
        return
    from core.product import Product
    from core.property_pool import PropertyPool
    from page.filler import FormFiller

    url = "file:///" + MOCK_PAGE.replace("\\", "/")
    product = Product.from_dict({
        "name": "日期测试",
        "target_url": url,
        "field_mapping": [
            {"property": "保险期间起", "locator": "#startValue",
             "fill_mode": "date", "required": True, "verify_after_fill": True},
            {"property": "学生姓名", "locator": "#name", "required": True},
        ],
        "verify_delay_ms": 100,
    })
    pool = PropertyPool()
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    pool.feed_dict({"保险期间起": tomorrow, "学生姓名": "测试学生"})

    try:
        # 直接调页面级接口（避免 Filler 自己开浏览器）
        page = browser.new_page()
        page.goto(url)
        filler = FormFiller(product, pool, browser_hold_seconds=0)
        report = filler.fill_on_page(page)
        status = {r["property"]: r["status"] for r in report}
        assert status.get("保险期间起") == "filled", report
        assert tomorrow in page.locator("#startValue").inner_text()
    finally:
        browser.close()
        playwright.stop()


if __name__ == "__main__":
    test_parse_iso_date()
    test_fill_date_on_mock_page()
    test_fill_date_corrects_off_by_one_picker()
    test_filler_integration()
    print("test_date_picker.py 全部通过 ✓")
