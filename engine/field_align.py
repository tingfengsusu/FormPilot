"""字段对齐：把「样例表的列」和「页面上扫到的字段」配起来 —— 自动建议，人来确认。

对应设计里的四段链前两段（《重构设计》13.1）：

    Excel 列 ──①列名字典──▶ 字段名 ──②属性──▶ 页面 locator

**这里只出建议，不做绑定**：每列算一个候选项（列名命中字典 / 页面标签命中 / 值形态），
带置信度与理由；配不上的显式标出来（"这一列没用上"要看得见），歧义给备选而不是硬选。

边界：字段名与别名由调用方（业务侧配置）传进来，引擎不认识具体业务；
值形态识别（身份证/手机/日期/枚举）是通用能力。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: 值形态（与字段/业务无关的通用识别）
SHAPE_ID_CARD = "id_card"
SHAPE_PHONE = "phone"
SHAPE_DATE = "date"
SHAPE_NUMBER = "number"
SHAPE_TEXT = "text"

_SPACES = re.compile(r"[\s\u3000]+")
_ID_CARD = re.compile(r"^\d{17}[\dXx]$")
_PHONE = re.compile(r"^1[3-9]\d{9}$")
_DATE = re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$|^\d{8}$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def normalize(text: Any) -> str:
    """归一化：去空白 + 常见分隔符统一（列名匹配用，与列名字典口径一致）。"""
    return _SPACES.sub("", str(text or "")).strip().lower()


def value_shape(value: Any) -> str:
    """猜一个值的形态（只看**整列样例都符合**才敢下结论，见 :func:`column_shape`）。"""
    text = str(value or "").strip()
    if not text:
        return SHAPE_TEXT
    if _ID_CARD.match(text):
        return SHAPE_ID_CARD
    if _PHONE.match(text):
        return SHAPE_PHONE
    if _DATE.match(text):
        return SHAPE_DATE
    if _NUMBER.match(text):
        return SHAPE_NUMBER
    return SHAPE_TEXT


def column_shape(values: Sequence[Any]) -> str:
    """整列判定：所有非空样例同一形态才算（避免一列里混着数字和文字就乱猜）。"""
    shapes = {value_shape(v) for v in values if str(v or "").strip()}
    if len(shapes) == 1:
        return shapes.pop()
    return SHAPE_TEXT


@dataclass
class ColumnSuggestion:
    """一列的对齐建议（界面按这个画：左边列、右边下拉、理由）"""

    column: str
    sample: str = ""
    field: str = ""                       # 建议字段名（字典键）
    locator: str = ""                     # 建议的页面 locator
    confidence: str = ""                  # 高 / 中 / 低 / ""（没配上）
    reasons: List[str] = dc_field(default_factory=list)
    transform: str = ""                   # 值形态推断的转换建议（如 date:YYYYMMDD）
    shape: str = SHAPE_TEXT
    used: bool = True                     # False = 没配上（界面标黄，列进"没用上"）
    candidates: List[str] = dc_field(default_factory=list)   # 歧义时的备选字段

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


def _match_fields(column: str, dictionary: Dict[str, Iterable[str]]) -> Tuple[str, List[str]]:
    """按列名在字典里找字段：精确 → 归一化 → 包含；返回 (最佳字段, 全部命中)。

    命中多个**不硬选**：全部返回，让 :func:`suggest_alignments` 标成歧义。
    """
    text = str(column or "").strip()
    norm = normalize(text)
    exact = [f for f, aliases in dictionary.items()
             if any(str(a).strip() == text for a in aliases)]
    if len(exact) == 1:
        return exact[0], exact
    normalized = [f for f, aliases in dictionary.items()
                  if any(normalize(a) == norm for a in aliases)]
    if len(normalized) == 1:
        return normalized[0], normalized
    if exact or normalized:
        hits = exact or normalized
        return hits[0], hits
    contains = [f for f, aliases in dictionary.items()
                if any(normalize(a) and normalize(a) in norm for a in aliases)]
    if not contains:
        return "", []
    return contains[0], contains


def _page_text(item: Dict[str, Any]) -> str:
    return normalize(" ".join(str(item.get(key) or "")
                              for key in ("label", "placeholder", "name", "id")))


def _find_page_field(fieldname: str, aliases: Iterable[str],
                     page_fields: Sequence[Dict[str, Any]],
                     column: str = "") -> Optional[Dict[str, Any]]:
    """在页面字段里找这个字段对应的控件（按 label/placeholder 与别名的包含关系）。"""
    probe = [normalize(a) for a in aliases if normalize(a)]
    probe += [normalize(fieldname), normalize(column)]
    probe = [p for p in probe if p]
    if not probe:
        return None
    best = None
    best_score = 0
    for item in page_fields:
        text = _page_text(item)
        if not text:
            continue
        score = sum(1 for p in probe if p and p in text)
        if score > best_score:
            best, best_score = item, score
    return best if best_score else None


def suggest_alignments(headers: Sequence[str],
                       sample_rows: Sequence[Dict[str, Any]],
                       page_fields: Sequence[Dict[str, Any]],
                       dictionary: Dict[str, Iterable[str]],
                       *, date_transform: str = "date:YYYY-MM-DD"
                       ) -> List[ColumnSuggestion]:
    """给每一列出建议。

    :param headers: 样例表表头
    :param sample_rows: 前几行数据（``{列名: 值}``，用于值形态判定）
    :param page_fields: 扫描到的页面字段（``{locator, label, placeholder, ...}``）
    :param dictionary: 列名字典（``{字段名: [别名...]}``）
    :param date_transform: 日期列默认建议的转换（页面若写了格式，由调用方按页面覆盖）
    """
    out: List[ColumnSuggestion] = []
    for header in headers:
        values = [row.get(header) for row in sample_rows or []]
        sample = next((str(v) for v in values if str(v or "").strip()), "")
        shape = column_shape(values)
        suggestion = ColumnSuggestion(column=str(header), sample=sample, shape=shape)

        fieldname, hits = _match_fields(header, dictionary)
        if fieldname:
            suggestion.field = fieldname
            if len(hits) > 1:
                suggestion.candidates = hits
                suggestion.reasons.append(
                    f"列名可能属于多个字段（{'/'.join(hits[:3])}），请确认")
        else:
            suggestion.reasons.append("列名不在列名字典里（确认后可收进字典）")

        if fieldname:
            aliases = list(dictionary.get(fieldname) or [])
            page_item = _find_page_field(fieldname, aliases, page_fields, column=header)
            if page_item:
                suggestion.locator = str(page_item.get("locator") or "")
                suggestion.reasons.append("页面标签命中")
                suggestion.confidence = "低" if suggestion.ambiguous else "高"
            else:
                suggestion.reasons.append("页面上没找到对应字段（可能不需要填）")

        # 值形态只做**提示**，不据此硬配对（不猜）
        if shape == SHAPE_DATE:
            suggestion.transform = date_transform
            suggestion.reasons.append("样例值都是日期 → 建议配「转换」")
        elif shape == SHAPE_ID_CARD:
            suggestion.reasons.append("样例值都是身份证 → 建议配校验位规则")
        elif shape == SHAPE_PHONE:
            suggestion.reasons.append("样例值都是手机号 → 建议配格式校验")

        suggestion.used = bool(suggestion.field and suggestion.locator)
        out.append(suggestion)
    return out


def unused_columns(suggestions: Sequence[ColumnSuggestion]) -> List[str]:
    """没用上的列（界面要显式列出来，别静默丢）。"""
    return [s.column for s in suggestions if not s.used]


def unmatched_page_fields(suggestions: Sequence[ColumnSuggestion],
                          page_fields: Sequence[Dict[str, Any]]) -> List[str]:
    """页面上有、但没有任何列（或建议）指向的控件 —— 这些要人补。"""
    bound = {s.locator for s in suggestions if s.locator}
    out = []
    for item in page_fields:
        locator = str(item.get("locator") or "")
        if locator and locator not in bound:
            label = item.get("label") or item.get("placeholder") or locator
            out.append(str(label))
    return out


__all__ = ["ColumnSuggestion", "suggest_alignments", "unused_columns",
           "unmatched_page_fields", "value_shape", "column_shape", "normalize"]
