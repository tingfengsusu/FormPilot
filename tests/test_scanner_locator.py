"""扫描器定位策略测试（placeholder 兜底 + 同名消歧 + 过滤不可填元素）。

背景：太保投保页等真实 H5 的输入框没有 name/id，只有 placeholder
（如「请录入姓名」）；桌面上下文还会被服务端拒绝。本测试用本地页面
复现这些特征，验证 locator 构造逻辑（不依赖外网）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>定位策略测试页</title></head>
<body>
  <!-- 只有 placeholder（真实太保页的形态） -->
  <input type="text" placeholder="请录入姓名">
  <input type="text" placeholder="请录入身份证">
  <!-- 同名 placeholder 重复（投保人/被保险人同名输入框） -->
  <input type="text" placeholder="请录入姓名">
  <!-- name 优先 -->
  <input type="text" name="xm" placeholder="请输入学生姓名">
  <!-- id 次优先 -->
  <input type="text" id="only_id" placeholder="请输入手机号">
  <!-- select 带 id -->
  <select id="plan"><option value="a">A</option></select>
  <!-- 应被过滤：隐藏 / 文件 / 不可见 / 无任何属性 -->
  <input type="hidden" name="hid" value="x">
  <input type="file" name="upload">
  <input type="text" placeholder="不可见的" style="display:none">
  <input type="text">
</body></html>
"""


def _scan_local(tmp_path):
    from core import config as config_module
    config_module.config._data = {"mock_mode": False}

    page_path = os.path.join(str(tmp_path), "form.html")
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(_PAGE)

    from page.scanner import Scanner
    url = "file:///" + page_path.replace("\\", "/")
    return Scanner().scan_or_raise(url)


def test_locator_strategy(tmp_path):
    from playwright.sync_api import sync_playwright  # noqa: F401

    try:
        fields = _scan_local(tmp_path)
    except ImportError:
        skip_test("未安装 playwright")
        return
    except Exception as e:  # noqa: BLE001
        skip_test(f"浏览器不可用（{str(e)[:60]}）")
        return

    locators = [f["locator"] for f in fields]

    # name 优先、id 次之、placeholder 兜底（含同名消歧）
    assert 'input[name="xm"]' in locators
    assert '#only_id' in locators
    assert '#plan' in locators
    assert 'input[placeholder="请录入身份证"]' in locators
    assert 'input[placeholder="请录入姓名"] >> nth=0' in locators
    assert 'input[placeholder="请录入姓名"] >> nth=1' in locators

    # 不可填写的元素被过滤：hidden / file / 不可见 / 无属性
    assert not any("hid" in loc for loc in locators)
    assert not any("upload" in loc for loc in locators)
    assert not any("不可见" in loc for loc in locators)
    assert len(locators) == 6, f"应只保留 6 个可填输入框: {locators}"

    # placeholder 同步带出，便于人工核对与 LLM 匹配
    by_loc = {f["locator"]: f for f in fields}
    assert by_loc['input[placeholder="请录入身份证"]']["placeholder"] == "请录入身份证"
    print(f"✓ 定位策略：{len(locators)} 个 locator 全部符合预期")


def test_context_config_roundtrip():
    """品种 context 配置的序列化（移动端 H5 场景）。"""
    from core.product import Product

    p = Product(name="t", target_url="https://x.com/a",
                context={"mobile": True, "referer": "https://x.com"})
    d = p.to_dict()
    assert d["context"] == {"mobile": True, "referer": "https://x.com"}
    assert Product.from_dict(d).context["mobile"] is True

    # 空 context 不写入 yaml（保持配置干净）
    p2 = Product(name="t2")
    assert "context" not in p2.to_dict()

    from page.browser_context import origin_of
    assert origin_of("https://dwx.cpic.com.cn/cxNew/x/?a=1") == "https://dwx.cpic.com.cn"
    assert origin_of("") == ""


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_locator_strategy(td)
    test_context_config_roundtrip()
    print("test_scanner_locator.py 全部通过 ✓")
