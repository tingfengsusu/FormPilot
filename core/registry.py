"""属性来源注册表（可插拔）。

通过注册表集中管理所有可用的属性来源，供 GUI 和填表流程按名称
（source name）查找。新增来源只需在此注册，无需改动核心代码。
"""
from __future__ import annotations

from typing import Dict, List, Type

from .property_source import PropertySource


class SourceRegistry:
    """属性来源注册表。"""

    def __init__(self) -> None:
        self._entries: Dict[str, PropertySource] = {}

    def register(self, source: PropertySource) -> None:
        """注册一个来源实例（按 source.name 作为键）。"""
        self._entries[source.name] = source

    def get(self, name: str) -> PropertySource:
        return self._entries[name]

    def names(self) -> List[str]:
        return list(self._entries.keys())

    def all(self) -> List[PropertySource]:
        return list(self._entries.values())


#: 全局默认注册表实例。
registry = SourceRegistry()