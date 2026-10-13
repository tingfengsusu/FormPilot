"""「沿用上次的值」：把标了沿用的字段值存在本地，下次自动带出来。

用在哪：重复性表单（这次和上次只差一点）、以及上次没填成的重跑。

两条原则：

1. **只记"填成功"的值**（回读零差异）——把错值沿用到下一次是最糟的
2. **存本地、不进品种 yaml**：品种是配置（会被 git 带走），值是业务数据，
   所以放 ``data/sticky_values.json``（已在 .gitignore 里）

用法::

    store = StickyStore()
    values = store.prefill(product)          # {属性: 上次的值}（只含标了沿用且有值的）
    store.remember(product, pool)            # 填成功后记下这些字段
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_PROJECT_ROOT, "data", "sticky_values.json")


class StickyStore:
    """按「品种名 → {属性: 值}」存上次成功填写的值。

    存储路径可用环境变量 ``FORMPILOT_STICKY_PATH`` 覆盖（测试/多套配置用）。
    """

    def __init__(self, path: str = ""):
        self.path = path or os.environ.get("FORMPILOT_STICKY_PATH") or _DEFAULT_PATH

    # ------------------------------------------------------------------
    def _read(self) -> Dict[str, Dict[str, Any]]:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):   # 文件不存在/损坏 → 当作空（不崩）
            return {}

    def _write(self, data: Dict[str, Dict[str, Any]]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)     # 原子替换：写一半被中断不会毁掉旧数据

    # ------------------------------------------------------------------
    def sticky_fields(self, product: Any) -> list:
        """该品种标了「沿用」的字段名。"""
        return [m.property for m in getattr(product, "field_mapping", []) or []
                if getattr(m, "sticky", False)]

    def prefill(self, product: Any) -> Dict[str, str]:
        """取该品种"沿用"字段的上次值（没有就返回空 dict）。"""
        fields = set(self.sticky_fields(product))
        if not fields:
            return {}
        saved = self._read().get(str(getattr(product, "name", "")), {}) or {}
        return {k: str(v) for k, v in saved.items() if k in fields and str(v) != ""}

    def remember(self, product: Any, pool: Any) -> Dict[str, str]:
        """把属性池里"沿用"字段的当前值记下来；返回记下的内容（空 = 没记）。"""
        fields = self.sticky_fields(product)
        if not fields:
            return {}
        values = {}
        for name in fields:
            try:
                value = pool.get(name)
            except Exception:  # noqa: BLE001 — 池子接口异常不该影响填写结果
                continue
            if value is not None and str(value).strip() != "":
                values[name] = str(value)
        if not values:
            return {}
        data = self._read()
        data.setdefault(str(getattr(product, "name", "")), {}).update(values)
        try:
            self._write(data)
        except OSError:
            return {}
        return values

    def forget(self, product_name: str) -> None:
        """清掉某品种记下的值。"""
        data = self._read()
        if product_name in data:
            data.pop(product_name, None)
            try:
                self._write(data)
            except OSError:
                pass


__all__ = ["StickyStore"]
