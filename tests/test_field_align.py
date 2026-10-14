"""字段对齐建议（纯逻辑，不碰浏览器/网络）。

场景：接入一个新页面时，"Excel 的哪一列填到页面的哪个字段"没有现成答案。
这里给建议（列名命中字典 / 页面标签命中 / 值形态提示），人工在界面上确认。

守住两条：
  - **不猜**：列名/页面都没命中就标"没用上"，不硬塞
  - **歧义给备选**：命中多个字段时列出来让人选，不按顺序静默取第一个
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.field_align import (SHAPE_DATE, SHAPE_ID_CARD, SHAPE_NUMBER,
                                SHAPE_PHONE, column_shape, suggest_alignments,
                                unmatched_page_fields, unused_columns,
                                value_shape)

#: 列名字典（业务侧会传真的进来；这里是测试用的最小样本）
_DICT = {
    "name": ["被保人姓名", "学生姓名", "姓名"],
    "id_card": ["被保人身份证", "学生身份证号", "身份证号", "身份证"],
    "phone": ["被保人手机号", "手机号", "联系电话"],
    "period_start": ["保险起期", "起始日期"],
}

#: 扫描到的页面字段（模拟 page/scanner.py 的输出结构）
_PAGE = [
    {"locator": 'input[placeholder="请录入姓名"]', "placeholder": "请录入姓名", "label": ""},
    {"locator": 'input[placeholder="请录入身份证"]', "placeholder": "请录入身份证", "label": ""},
    {"locator": "div.grade", "placeholder": "", "label": "年级"},
]


def test_value_shape_recognises_common_forms():
    assert value_shape("410305201509011230") == SHAPE_ID_CARD
    assert value_shape("41030520150901123X") == SHAPE_ID_CARD
    assert value_shape("13900000000") == SHAPE_PHONE
    assert value_shape("2026-10-20") == SHAPE_DATE
    assert value_shape("20261020") == SHAPE_DATE
    assert value_shape("1130.00") == SHAPE_NUMBER
    assert value_shape("张小明") == "text"


def test_column_shape_needs_all_samples_same_form():
    assert column_shape(["2026-10-20", "2026-10-21"]) == SHAPE_DATE
    assert column_shape(["2026-10-20", "待定"]) == "text"      # 混着就不猜
    assert column_shape([]) == "text"


def test_suggests_column_to_page_field_with_reason():
    headers = ["被保人姓名", "被保人身份证", "缴费状态"]
    rows = [{"被保人姓名": "张小明", "被保人身份证": "410305201509011230",
             "缴费状态": "已缴"}]

    suggestions = {s.column: s for s in
                   suggest_alignments(headers, rows, _PAGE, _DICT)}

    name = suggestions["被保人姓名"]
    assert name.field == "name" and name.locator.startswith('input[placeholder="请录入姓名"]')
    assert name.used and name.confidence == "高"
    assert "页面标签命中" in name.reasons

    id_card = suggestions["被保人身份证"]
    assert id_card.field == "id_card" and id_card.shape == SHAPE_ID_CARD
    assert any("身份证" in r for r in id_card.reasons)       # 值形态提示

    # 「缴费状态」既不在字典、页面也没有 → 标成没用上，**不硬塞**
    unpaid = suggestions["缴费状态"]
    assert not unpaid.used and unpaid.field == ""
    assert unused_columns(list(suggestions.values())) == ["缴费状态"]


def test_date_column_gets_transform_hint():
    headers = ["起始日期"]
    rows = [{"起始日期": "2026-10-20"}, {"起始日期": "2026/11/01"}]
    suggestion = suggest_alignments(headers, rows, _PAGE, _DICT)[0]

    assert suggestion.shape == SHAPE_DATE
    assert suggestion.transform == "date:YYYY-MM-DD"


def test_ambiguous_column_lists_candidates_instead_of_picking():
    """同一个别名被两个字段声明时（容易被写错）给备选，不静默取第一个。"""
    dictionary = {
        # "身份证" 被两个字段都声明了 —— 这就是真实歧义的来源
        "payer_id_card": ["缴款人身份证", "身份证", "家长身份证"],
        "id_card": ["被保人身份证", "身份证", "学生身份证号"],
    }
    suggestion = suggest_alignments(["身份证"], [{"身份证": "410305201509011230"}],
                                    _PAGE, dictionary)[0]

    assert set(suggestion.candidates) == {"payer_id_card", "id_card"}
    assert suggestion.ambiguous
    assert suggestion.confidence == "低"          # 歧义不给高置信
    assert any("请确认" in r for r in suggestion.reasons)


def test_exact_alias_hit_is_not_ambiguity():
    """列名精确等于某字段的别名 → 直接定下来（这是字典本来就写对了的情况）。"""
    suggestion = suggest_alignments(["被保人身份证"],
                                    [{"被保人身份证": "410305201509011230"}],
                                    _PAGE, _DICT)[0]

    assert suggestion.field == "id_card"
    assert not suggestion.ambiguous


def test_reports_page_fields_nobody_claims():
    headers = ["被保人姓名"]
    rows = [{"被保人姓名": "张小明"}]
    suggestions = suggest_alignments(headers, rows, _PAGE, _DICT)

    missing = unmatched_page_fields(suggestions, _PAGE)
    assert "请录入身份证" in " ".join(missing) and "年级" in " ".join(missing)


if __name__ == "__main__":
    test_value_shape_recognises_common_forms()
    test_column_shape_needs_all_samples_same_form()
    test_suggests_column_to_page_field_with_reason()
    test_date_column_gets_transform_hint()
    test_ambiguous_column_lists_candidates_instead_of_picking()
    test_exact_alias_hit_is_not_ambiguity()
    test_reports_page_fields_nobody_claims()
    print("test_field_align.py 全部通过 ✓")
