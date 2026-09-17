"""来源工厂 — 按显示名创建 PropertySource 实例。

GUI 属性面板挂「来源组」时，通过这里拿到对应的来源实例，
从而调用 extract() 实现拖图识别。
"""
from __future__ import annotations

from typing import Optional

from core.property_source import PropertySource
from .global_user_info import GlobalUserInfo
from .idcard_ocr import IdCardOCR
from .household_ocr import HouseholdOCR
from .excel_source import ExcelSource


#: 显示名 → 来源类的映射。GUI 的分组名必须与之一致。
_SOURCE_CLASSES = {
    "身份证OCR": IdCardOCR,
    "户口本OCR": HouseholdOCR,
    "Excel名单": ExcelSource,
    "保险员信息": GlobalUserInfo,
}

#: 内置来源的显示名列表（顺序即属性面板/向导中的展示顺序）。
AVAILABLE_BUILTIN_SOURCES = list(_SOURCE_CLASSES.keys())


def create_source(source_name: str) -> Optional[PropertySource]:
    """按显示名创建来源实例；未知来源返回 None。"""
    cls = _SOURCE_CLASSES.get(source_name)
    return cls() if cls else None


def get_fields_for_source(source_name: str) -> list[str]:
    """返回某来源能提供的字段名列表。"""
    src = create_source(source_name)
    return src.get_fields() if src else []