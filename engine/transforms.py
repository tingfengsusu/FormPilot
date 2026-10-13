"""值转换：把数据源里的值规范成页面要求的格式（业务无关的通用规则）。

为什么需要（实测发现）：数据里是 ``2026-10-20``，而页面要 ``YYYYMMDD``
（发票查验平台、部分政务系统就是这样），直接填会被拒或填成错的日期。

规则名写在 ``FieldMapping.transform``（品种 yaml 的 ``transform:``）：

- ``""``            不转换
- ``strip``         去首尾空白（含全角空格）
- ``digits``        只留数字（手机号/金额里混了空格、短横线时用）
- ``date:YYYYMMDD`` / ``date:YYYY-MM-DD`` / ``date:YYYY/MM/DD`` / ``date:YYYY年MM月DD日``
  先**宽容解析**各种日期写法，再按目标格式输出；解析不了就原样返回（不猜）

品种编辑页扫描页面字段时会按 placeholder 自动建议（如 ``YYYYMMDD``），
默认选中——用户不用自己去发现这个坑。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

#: 可选规则（品种编辑页的下拉用；顺序即展示顺序）
TRANSFORMS = (
    ("", "不转换"),
    ("strip", "去空格"),
    ("digits", "只留数字"),
    ("date:YYYY-MM-DD", "日期 → 2026-10-20"),
    ("date:YYYYMMDD", "日期 → 20261020"),
    ("date:YYYY/MM/DD", "日期 → 2026/10/20"),
    ("date:YYYY年MM月DD日", "日期 → 2026年10月20日"),
)

_SPACES = re.compile(r"[\s\u3000]+")
_DATE_PATTERNS = (
    re.compile(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"),
    re.compile(r"^(\d{4})(\d{2})(\d{2})$"),          # 20261020
)


def parse_date(text: Any) -> Optional[date]:
    """宽容解析常见日期写法；解析不了返回 None（不猜）。"""
    if text is None:
        return None
    raw = _SPACES.sub("", str(text)).strip()
    if not raw:
        return None
    for pattern in _DATE_PATTERNS:
        m = pattern.search(raw)
        if not m:
            continue
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def format_date(value: date, pattern: str) -> str:
    """按目标格式输出日期（只支持我们列出来的几种，别的原样返回）。"""
    table = {
        "YYYYMMDD": f"{value.year:04d}{value.month:02d}{value.day:02d}",
        "YYYY-MM-DD": f"{value.year:04d}-{value.month:02d}-{value.day:02d}",
        "YYYY/MM/DD": f"{value.year:04d}/{value.month:02d}/{value.day:02d}",
        "YYYY年MM月DD日": f"{value.year:04d}年{value.month:02d}月{value.day:02d}日",
    }
    return table.get(pattern, "")


def apply_transform(value: Any, rule: str) -> str:
    """按规则转换一个值；规则为空或无法解析时**原样返回**。"""
    text = "" if value is None else str(value)
    rule = (rule or "").strip()
    if not rule:
        return text
    if rule == "strip":
        return _SPACES.sub("", text).strip()
    if rule == "digits":
        return re.sub(r"\D", "", text)
    if rule.startswith("date:"):
        parsed = parse_date(text)
        if parsed is None:
            return text
        formatted = format_date(parsed, rule.split(":", 1)[1].strip())
        return formatted or text
    return text


def suggest_transform(placeholder: str = "", label: str = "") -> str:
    """按页面提示猜一个转换规则（品种编辑页扫描后用；猜不出返回空串）。

    只认**明确的格式提示**，不靠"日期"两个字猜 —— 猜错比不猜更糟。
    判定顺序要紧：带分隔符的写法先判，否则 ``yyyy-MM-dd`` 会被压成 ``yyyymmdd``
    误判成紧凑格式（测试守住了这条）。
    """
    hints = f"{placeholder or ''} {label or ''}".lower()
    compact = hints.replace("-", "").replace("/", "").replace(" ", "")
    if "yyyy-mm-dd" in hints or "yyyy/mm/dd" in hints:
        return "date:YYYY-MM-DD"
    if "yyyy年mm月dd日" in hints:
        return "date:YYYY年MM月DD日"
    if "yyyymmdd" in compact:
        return "date:YYYYMMDD"
    return ""


__all__ = ["TRANSFORMS", "apply_transform", "parse_date", "format_date",
           "suggest_transform"]
