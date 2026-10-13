"""engine 包自身测试：Mock 会话，**不碰浏览器、不碰网络**。

覆盖（对应 docs/引擎规格-FillEngine.md 第七节验收清单）：

  - Mock 会话填一条：ok / attempts / 不产截图 / 已填字段数
  - 必填缺值 → 失败 + 问题清单（问题里带字段名，且已截断）
  - 重试到上限：attempts == max_attempts
  - 事件流：session_started / record_started / field_verified / record_finished / session_closed
  - 预检：缺映射的必填字段（不该开浏览器）
  - 提交默认禁止；FieldKind 收拢 fill_mode 取值
"""
from __future__ import annotations

import os
import sys
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import browser_available, skip_test

from core.product import Product
from core.property_pool import PropertyPool
from engine import (FieldKind, FillEngine, FillEvent, SubmitSpec,
                    missing_mappings)
from engine.session import problems_of, safe_filename


def _data_page(body: str) -> str:
    """把一小段 HTML 变成 data: URL（中文要百分号编码，否则浏览器可能不认）。"""
    return "data:text/html;charset=utf-8," + quote(f"<html><body>{body}</body></html>")


def _product(**kwargs) -> Product:
    data = {
        "name": "测试品种",
        "target_url": "https://example.com/form",
        "field_mapping": [
            {"property": "姓名", "locator": 'input[name="xm"]'},
            {"property": "手机号", "locator": 'input[name="sjh"]'},
            {"property": "备注", "locator": 'input[name="bz"]', "required": False},
        ],
    }
    data.update(kwargs)
    return Product.from_dict(data)


def _pool(**kwargs) -> PropertyPool:
    pool = PropertyPool()
    pool.feed_dict({"姓名": "张三", "手机号": "13800000000", **kwargs})
    return pool


def test_mock_session_fills_one_record():
    engine = FillEngine(_product(), mock=True)
    with engine.session() as session:
        result = session.fill(_pool())

    assert result.ok and result.attempts == 1
    assert result.screenshot == ""            # Mock 无页面 → 不产截图
    assert result.filled_fields == 2          # 姓名 + 手机号（备注无值且非必填）
    assert result.problems == [] and result.diffs == []
    assert result.submitted is False


def test_required_missing_reports_problem():
    engine = FillEngine(_product(), mock=True)
    result = engine.fill_one(_pool(姓名=""), max_attempts=2, backoff_seconds=0)

    assert not result.ok and result.attempts == 2
    assert result.problems and "姓名" in result.problems[0]


def test_retry_exhaustion_keeps_attempts():
    engine = FillEngine(_product(), mock=True)
    result = engine.fill_one(_pool(手机号=""), max_attempts=3, backoff_seconds=0)

    assert result.attempts == 3 and not result.ok
    assert any("手机号" in problem for problem in result.problems)


def test_events_are_emitted():
    seen = []
    engine = FillEngine(_product(), mock=True,
                        on_event=lambda event, data: seen.append(event))
    engine.fill_one(_pool())

    for expected in (FillEvent.SESSION_STARTED, FillEvent.RECORD_STARTED,
                     FillEvent.FIELD_VERIFIED, FillEvent.RECORD_FINISHED,
                     FillEvent.SESSION_CLOSED):
        assert expected in seen, f"缺事件：{expected}"


def test_event_hook_exception_does_not_break_fill():
    def boom(event, data):
        raise RuntimeError("观察者炸了")

    engine = FillEngine(_product(), mock=True, on_event=boom)
    assert engine.fill_one(_pool()).ok            # 回调异常不影响填写


def test_preflight_reports_missing_mappings():
    product = _product()
    assert missing_mappings(product, ["姓名", "不存在的字段"]) == ["不存在的字段"]
    assert FillEngine(product, mock=True).preflight(["姓名", "手机号"]) == []


def test_submit_forbidden_by_default():
    assert SubmitSpec().allowed is False
    assert SubmitSpec(allowed=True).allowed is True


def test_field_kind_maps_fill_mode():
    assert FieldKind.of("select") is FieldKind.SELECT
    assert FieldKind.of("verify_only") is FieldKind.VERIFY_ONLY
    assert FieldKind.of("") is FieldKind.PLAIN
    assert FieldKind.of(None) is FieldKind.PLAIN
    assert FieldKind.of("不认识的模式") is FieldKind.PLAIN


def test_problems_of_truncates_long_messages():
    report = [{"property": "X", "status": "error", "message": "长" * 500},
              {"property": "Y", "status": "filled", "message": "正常"}]
    problems = problems_of(report)

    assert len(problems) == 1
    assert len(problems[0]) < 200             # 截断到 160 字，防止挤掉后面的问题
    assert problems_of([]) == []


def test_safe_filename_strips_illegal_chars():
    assert safe_filename("学生/甲:1") == "学生甲1"
    assert safe_filename("") == "record"


# ----------------------------------------------------------------------
# 页面拦截（验证码 / 滑块 / 登录墙）—— 真浏览器，页面是 data: URL
# ----------------------------------------------------------------------
def _blocker_product(html: str) -> Product:
    return Product.from_dict({
        "name": "拦截页测试", "target_url": html, "verify_delay_ms": 10,
        "field_mapping": [{"property": "姓名", "locator": 'input[name="xm"]'}],
    })


def test_captcha_page_stops_and_needs_human():
    """页面有验证码/滑块 → 不填、不重试、不提交，直接转人工。"""
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    html = _data_page('<input name="xm"><div>请拖动滑块完成验证</div>')
    pool = PropertyPool()
    pool.feed_dict({"姓名": "张三"})

    events = []
    engine = FillEngine(_blocker_product(html), headless=True,
                        on_event=lambda event, data: events.append(event))
    result = engine.fill_one(pool, max_attempts=3, backoff_seconds=0)

    assert not result.ok
    assert result.needs_human                      # 转人工，且原因可读
    assert result.attempts == 1                    # 验证码不重试（重试也过不去）
    assert result.filled_fields == 0               # 一个字都没往里填
    assert FillEvent.BLOCKED in events


def test_captcha_field_does_not_block_filling():
    """页面只是有个验证码框（发票查验就是）→ 其余字段照填，验证码留人工。"""
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    html = _data_page(
        '<input name="fpdm" placeholder="请输入">'
        '<div class="form-item-yzm"><label>*验证码</label>'
        '<input name="yzm_code" placeholder="请输入"><img src="/captcha.jpg"></div>')
    product = Product.from_dict({
        "name": "验证码字段页", "target_url": html, "verify_delay_ms": 10,
        "field_mapping": [{"property": "发票代码", "locator": 'input[name="fpdm"]'}],
    })
    pool = PropertyPool()
    pool.feed_dict({"发票代码": "011002100311"})

    result = FillEngine(product, headless=True).fill_one(pool)

    assert result.ok                       # 没被拦，正常填完
    assert not result.needs_human          # 不是"整页墙"
    assert "验证码" in result.manual_hint   # 但提醒人工补这一步
    assert result.filled_fields == 1


def test_plain_word_does_not_block():
    """正文里只是提到"验证码"（如 12306）不该误拦 —— 不确定就不拦。"""
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    html = _data_page('<input name="xm"><div>登录后可查看验证码说明</div>')
    pool = PropertyPool()
    pool.feed_dict({"姓名": "张三"})
    result = FillEngine(_blocker_product(html), headless=True).fill_one(pool)

    assert result.ok and not result.needs_human


if __name__ == "__main__":
    test_mock_session_fills_one_record()
    test_required_missing_reports_problem()
    test_retry_exhaustion_keeps_attempts()
    test_events_are_emitted()
    test_event_hook_exception_does_not_break_fill()
    test_preflight_reports_missing_mappings()
    test_submit_forbidden_by_default()
    test_field_kind_maps_fill_mode()
    test_problems_of_truncates_long_messages()
    test_safe_filename_strips_illegal_chars()
    test_captcha_page_stops_and_needs_human()
    test_captcha_field_does_not_block_filling()
    test_plain_word_does_not_block()
    print("test_engine_session.py 全部通过 ✓")
