"""属性对象模型与属性库。

每个属性不再是裸字符串，而是一个结构化对象 :class:`PropertyDef`：
属性名 / 归属来源 / 字段类型 / 选项 / 默认值 / 必填 / 描述。

属性库分两层：
  - 内置属性：由各 PropertySource 的 get_fields() 推导（带类型启发式推断）
  - 用户自定义：持久化在 config/property_library.yaml，可新增、可覆盖内置

合并规则（all_defs）：用户定义按属性名覆盖内置定义，其余内置属性照常提供。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

# 项目根目录（core 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_LIBRARY_PATH = os.path.join(_PROJECT_ROOT, "config", "property_library.yaml")

#: 支持的字段类型标识。
TYPE_TEXT = "text"
TYPE_NUMBER = "number"
TYPE_DATE = "date"
TYPE_SELECT = "select"
TYPE_PHONE = "phone"

#: 类型标识 → 中文显示名。
TYPE_LABELS: Dict[str, str] = {
    TYPE_TEXT: "文本",
    TYPE_NUMBER: "数字",
    TYPE_DATE: "日期",
    TYPE_SELECT: "下拉选择",
    TYPE_PHONE: "电话",
}

#: 属性可归属的来源组（含「自定义」兜底组）。
SOURCES = ["身份证OCR", "户口本OCR", "Excel名单", "保险员信息", "自定义"]


@dataclass
class PropertyDef:
    """属性对象 — 一个属性的完整定义。"""

    name: str                       # 属性名（唯一键），如 "学生姓名"
    source: str = "自定义"           # 归属来源组（GUI 分组用）
    field_type: str = TYPE_TEXT     # 字段类型：text/number/date/select/phone
    options: List[str] = field(default_factory=list)  # select 类型的候选项
    default_value: str = ""         # 默认值（录入面板预填）
    required: bool = False          # 是否必填
    description: str = ""           # 描述

    @classmethod
    def from_dict(cls, data: dict) -> "PropertyDef":
        return cls(
            name=str(data.get("name", "")),
            source=data.get("source", "自定义") or "自定义",
            field_type=data.get("field_type", TYPE_TEXT) or TYPE_TEXT,
            options=[str(o) for o in (data.get("options") or [])],
            default_value=str(data.get("default_value", "") or ""),
            required=bool(data.get("required", False)),
            description=str(data.get("description", "") or ""),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "source": self.source,
            "field_type": self.field_type,
            "options": list(self.options),
            "default_value": self.default_value,
            "required": self.required,
            "description": self.description,
        }


def _infer_field_type(prop_name: str) -> tuple[str, List[str]]:
    """按属性名启发式推断字段类型（内置属性用）。

    :return: (field_type, options)
    """
    if prop_name == "性别":
        return TYPE_SELECT, ["男", "女"]
    if "出生日期" in prop_name:
        return TYPE_DATE, []
    if "电话" in prop_name or "手机" in prop_name:
        return TYPE_PHONE, []
    if "工号" in prop_name or "户号" in prop_name or "代码" in prop_name:
        return TYPE_NUMBER, []
    return TYPE_TEXT, []


def builtin_defs() -> List[PropertyDef]:
    """从各内置来源的 get_fields() 推导内置属性对象。"""
    from sources.factory import AVAILABLE_BUILTIN_SOURCES, get_fields_for_source

    defs: List[PropertyDef] = []
    for source_name in AVAILABLE_BUILTIN_SOURCES:
        for prop in get_fields_for_source(source_name):
            ftype, options = _infer_field_type(prop)
            defs.append(PropertyDef(
                name=prop,
                source=source_name,
                field_type=ftype,
                options=options,
            ))
    return defs


class PropertyDefLibrary:
    """属性库：内置属性 + 用户自定义（config/property_library.yaml）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _DEFAULT_LIBRARY_PATH

    # ------------------------------------------------------------------
    # 用户自定义层（yaml 持久化）
    # ------------------------------------------------------------------
    def _load_user_defs(self) -> List[PropertyDef]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return [PropertyDef.from_dict(d) for d in data.get("properties", [])]

    def _save_user_defs(self, defs: List[PropertyDef]) -> None:
        serialized = {"properties": [d.to_dict() for d in defs]}
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.safe_dump(serialized, f, allow_unicode=True, sort_keys=False)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def all_defs(self) -> List[PropertyDef]:
        """全部属性定义：内置 + 用户自定义（同名时用户定义覆盖内置）。"""
        merged: Dict[str, PropertyDef] = {d.name: d for d in builtin_defs()}
        for d in self._load_user_defs():
            merged[d.name] = d
        return list(merged.values())

    def get(self, name: str) -> Optional[PropertyDef]:
        """按属性名取定义；不存在返回 None。"""
        for d in self.all_defs():
            if d.name == name:
                return d
        return None

    def upsert(self, prop: PropertyDef) -> None:
        """新增或更新一个用户自定义属性（同名内置属性即视为覆盖）。"""
        if not prop.name:
            raise ValueError("属性名不能为空")
        if prop.field_type == TYPE_SELECT and not prop.options:
            raise ValueError("下拉选择类型必须提供选项")

        defs = [d for d in self._load_user_defs() if d.name != prop.name]
        defs.append(prop)
        self._save_user_defs(defs)

    def remove(self, name: str) -> bool:
        """删除一个用户自定义属性。

        内置属性无法删除（只能覆盖），返回 False 表示库里没有可删的定义。
        """
        defs = self._load_user_defs()
        remaining = [d for d in defs if d.name != name]
        if len(remaining) == len(defs):
            return False
        self._save_user_defs(remaining)
        return True

    def save_all(self, defs: List[PropertyDef]) -> None:
        """整体替换用户定义层（属性管理页面「保存」用）。

        传入的列表即最终的用户层内容：页面上与内置默认一致的行不在列表中，
        等价于「没有覆盖」。
        """
        for d in defs:
            if not d.name:
                raise ValueError("属性名不能为空")
            if d.field_type == TYPE_SELECT and not d.options:
                raise ValueError(f"下拉选择类型必须提供选项：{d.name}")
        self._save_user_defs(defs)

    def user_def_names(self) -> set:
        """用户自定义/覆盖过的属性名集合。"""
        return {d.name for d in self._load_user_defs()}

    def is_builtin(self, name: str) -> bool:
        """是否为内置属性（未被用户覆盖过）。"""
        return any(d.name == name for d in builtin_defs()) and not any(
            d.name == name for d in self._load_user_defs())
