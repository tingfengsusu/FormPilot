"""LLM 字段匹配器测试（启发式匹配路径）。

注意：显式固定 mock_mode，确保测的是「启发式匹配」而不是真实 LLM 通道
（settings.yaml 配了 llm.mode=web 时会走网页版 DeepSeek，慢且依赖网络）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as config_module
from page.matcher import FieldMatcher
from page.scanner import Scanner


def test_matcher_heuristic():
    config_module.config._data = {"mock_mode": True}  # 固定走启发式

    matcher = FieldMatcher()
    page_fields = Scanner().scan("dummy")
    product_fields = ["学生姓名", "学生身份证号", "监护人电话", "保险方案"]

    result = matcher.match(page_fields, product_fields)
    assert result["学生姓名"] == 'input[name="xm"]'
    assert result["学生身份证号"] == 'input[name="sfzh"]'
    assert result["监护人电话"] == 'input[name="lxdh"]'
    assert result["保险方案"] == 'select[name="baoxian"]'


if __name__ == "__main__":
    test_matcher_heuristic()
    print("test_matcher.py 全部通过 ✓")