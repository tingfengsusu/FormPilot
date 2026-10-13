"""FillEngine：引擎对外唯一入口（规格 §二/§三）。

业务侧用法（批量：一条会话连续填多条）::

    engine = FillEngine(product, mock=False)
    with engine.session() as session:
        result = session.fill(pool, submit=False, screenshot_path="...")

单条场景用 :meth:`FillEngine.fill_one` 一把梭（起会话 → 填 → 关）。
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional, Sequence

from .models import FillResult
from .session import EventHook, FillSession


def missing_mappings(product: Any,
                     required_properties: Sequence[Any] = ()) -> List[str]:
    """品种里缺哪些必填字段的映射（缺了就不该开始填写，避免填半张表）。"""
    mapped = {mapping.property
              for mapping in getattr(product, "field_mapping", []) or []}
    return [str(prop) for prop in (required_properties or [])
            if str(prop) not in mapped]


class FillEngine:
    """引擎门面：会话工厂 + 单条便捷入口 + 预检。"""

    def __init__(self, product: Any, *, headless: bool = False,
                 mock: bool = False,
                 filler_factory: Optional[Callable[..., Any]] = None,
                 on_event: Optional[EventHook] = None):
        self.product = product
        self.headless = headless
        self.mock = mock
        self.filler_factory = filler_factory
        self.on_event = on_event

    def session(self) -> FillSession:
        """新建一条会话（用 ``with`` 管生命周期）。"""
        return FillSession(self.product, headless=self.headless, mock=self.mock,
                           filler_factory=self.filler_factory,
                           on_event=self.on_event)

    def preflight(self, required_properties: Sequence[Any] = ()) -> List[str]:
        """配置预检：缺映射的必填字段（非空 = 不该开浏览器）。"""
        return missing_mappings(self.product, required_properties)

    def fill_one(self, pool: Any, **fill_kwargs: Any) -> FillResult:
        """填一条记录（自建会话、用完即关）——单条录入/消息链路用。"""
        with self.session() as session:
            return session.fill(pool, **fill_kwargs)


__all__ = ["FillEngine", "missing_mappings", "FillSession", "FillResult"]
