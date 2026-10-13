"""值转换测试（纯逻辑，不碰浏览器、不碰网络）。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.transforms import (apply_transform, format_date, parse_date,
                               suggest_transform)


def test_parse_date_accepts_common_chinese_writings():
    for text in ("2026-10-20", "2026/10/20", "2026.10.20", "20261020",
                 "2026年10月20日", " 2026-10-20 ", "2026-1-2"):
        assert parse_date(text) is not None, text
    assert parse_date("2026-13-01") is None      # 月份非法
    assert parse_date("下周三") is None          # 不猜
    assert parse_date("") is None and parse_date(None) is None


def test_apply_transform_date_to_page_format():
    """数据里是 2026-10-20，页面要 YYYYMMDD（发票查验就是这么要的）。"""
    assert apply_transform("2026-10-20", "date:YYYYMMDD") == "20261020"
    assert apply_transform("2026年10月20日", "date:YYYY-MM-DD") == "2026-10-20"
    assert apply_transform("20261020", "date:YYYY/MM/DD") == "2026/10/20"
    assert apply_transform("2026-10-20", "date:YYYY年MM月DD日") == "2026年10月20日"


def test_apply_transform_is_idempotent_and_safe():
    once = apply_transform("2026-10-20", "date:YYYYMMDD")
    assert apply_transform(once, "date:YYYYMMDD") == once     # 重试时再转一次不变
    assert apply_transform("不是日期", "date:YYYYMMDD") == "不是日期"   # 解析不了原样返回
    assert apply_transform(None, "date:YYYYMMDD") == ""
    assert apply_transform(" 138 0000 0000 ", "digits") == "13800000000"
    assert apply_transform("  张 三　 ", "strip") == "张三"
    assert apply_transform("任意", "") == "任意"


def test_format_date_unknown_pattern_returns_empty():
    from datetime import date
    assert format_date(date(2026, 10, 20), "YYYYMMDD") == "20261020"
    assert format_date(date(2026, 10, 20), "DD/MM/YYYY") == ""   # 没实现的格式不硬编


def test_suggest_transform_only_on_explicit_hints():
    assert suggest_transform("YYYYMMDD", "*开票日期") == "date:YYYYMMDD"
    assert suggest_transform("yyyy-MM-dd", "日期") == "date:YYYY-MM-DD"
    assert suggest_transform("请输入", "开票日期") == ""     # 只写"日期"不猜
    assert suggest_transform("", "") == ""


if __name__ == "__main__":
    test_parse_date_accepts_common_chinese_writings()
    test_apply_transform_date_to_page_format()
    test_apply_transform_is_idempotent_and_safe()
    test_format_date_unknown_pattern_returns_empty()
    test_suggest_transform_only_on_explicit_hints()
    print("test_transforms.py 全部通过 ✓")
