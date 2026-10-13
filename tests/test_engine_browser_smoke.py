"""引擎真实浏览器冒烟：data: URL 起真 Chromium，验证会话生命周期 + 填写 + 回读 + 截图。

**不访问任何外部系统**（页面就是 data: URL 里的一小段 HTML），
未装 Playwright 或浏览器内核未下载时自动跳过（计为跳过而非失败）。

对应 docs/引擎规格-FillEngine.md 第七节：真实 driver 必须走同一套引擎入口。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

from core.product import Product
from core.property_pool import PropertyPool
from engine import FillEngine

_PAGE = ('data:text/html;charset=utf-8,'
         '<html><body>'
         '<input name="xm" placeholder="请录入姓名">'
         '</body></html>')


def _product() -> Product:
    return Product.from_dict({
        "name": "引擎冒烟",
        "target_url": _PAGE,
        "verify_delay_ms": 50,
        "field_mapping": [
            {"property": "姓名", "locator": 'input[name="xm"]'},
        ],
    })


def _browser_available() -> bool:
    """探测浏览器内核能否启动（未下载 / 缺依赖 → 跳过而不是失败）。"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def test_engine_real_browser_fill_and_screenshot():
    if not _browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    pool = PropertyPool()
    pool.feed_dict({"姓名": "张三"})
    engine = FillEngine(_product(), headless=True)

    with tempfile.TemporaryDirectory() as td:
        shot = os.path.join(td, "shot.png")
        with engine.session() as session:
            result = session.fill(pool, screenshot_path=shot)

        assert result.ok, result.problems
        assert result.filled_fields == 1        # 真页面回读一致才算成功
        assert result.submitted is False        # 提交默认禁止
        assert result.screenshot == shot
        assert os.path.exists(shot)             # 截图真的落盘了


def test_engine_real_browser_reports_missing_required():
    if not _browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    pool = PropertyPool()                       # 不给值 → 必填缺失
    engine = FillEngine(_product(), headless=True)
    result = engine.fill_one(pool, max_attempts=2, backoff_seconds=0)

    assert not result.ok and result.attempts == 2
    assert any("姓名" in problem for problem in result.problems)


if __name__ == "__main__":
    test_engine_real_browser_fill_and_screenshot()
    test_engine_real_browser_reports_missing_required()
    print("test_engine_browser_smoke.py 全部通过 ✓")
