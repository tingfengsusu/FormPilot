"""属性池管理器。

属性池是「运行时内存对象」：持有所有已解析的属性值，供后续填表时读取。
"""
from __future__ import annotations

from typing import Any, Dict

from .property_source import PropertySource


class PropertyPool:
    """属性池 — 运行时持有所有已解析的属性。"""

    def __init__(self) -> None:
        self._sources: Dict[str, PropertySource] = {}
        self._values: Dict[str, Any] = {}  # property_name -> value

    def register_source(self, source: PropertySource) -> None:
        """注册一个属性来源。"""
        self._sources[source.name] = source

    def feed(self, source_name: str, input_data: Any) -> Dict[str, Any]:
        """喂入数据：调用对应来源的 extract()，把结果写入属性池。

        :return: 本次解析出的属性字典
        """
        source = self._sources.get(source_name)
        if source is None:
            raise ValueError(f"未知的属性来源: {source_name}")

        extracted = source.extract(input_data)
        self._values.update(extracted)
        return extracted

    def feed_dict(self, values: Dict[str, Any]) -> None:
        """直接喂入一个属性字典（用于 Mock / 手动编辑）。"""
        self._values.update(values)

    def get(self, property_name: str) -> Any:
        """按属性名读取值；不存在则返回 None。"""
        return self._values.get(property_name)

    def all(self) -> Dict[str, Any]:
        """返回全部属性值。"""
        return dict(self._values)

    def is_complete(self, property_names: list[str]) -> tuple[bool, list[str]]:
        """检查给定属性名是否都已填充。

        :return: (是否完整, 缺失属性名列表)
        """
        missing = [name for name in property_names if not self._values.get(name)]
        return (len(missing) == 0), missing

    def clear(self) -> None:
        self._values.clear()