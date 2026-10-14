"""列名字典的合并与回写：机器学到的别名攒进本地覆盖文件，不动人手写的那份。

为什么要分两层：

- ``config/import_mapping.yaml`` 是**人手写**的，带注释和实测说明（"实测数据源列结构…"）
- 程序回写会把注释和排版冲掉 → 所以新认出来的列名写进同目录的
  ``import_mapping.local.yaml``，加载时 base + local 合并（local 只追加别名）

这样"字典会自己长大"，而人写的文件保持干净、可读、可 review。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import yaml


def local_path_for(mapping_path: str) -> str:
    """``import_mapping.yaml`` → ``import_mapping.local.yaml``。"""
    root, ext = os.path.splitext(mapping_path)
    return f"{root}.local{ext or '.yaml'}"


def merge(base: Dict[str, Any], local: Dict[str, Any]) -> Dict[str, Any]:
    """把本地别名并进基础字典：别名去重追加（保持 base 顺序），忽略列取并集。"""
    if not local:
        return dict(base or {})
    merged: Dict[str, Any] = {k: (list(v) if isinstance(v, list) else v)
                              for k, v in (base or {}).items()}

    for fieldname, aliases in (local.get("columns") or {}).items():
        target = merged.setdefault("columns", {}).setdefault(fieldname, [])
        if not isinstance(target, list):
            continue
        for alias in aliases or []:
            text = str(alias).strip()
            if text and text not in target:
                target.append(text)

    ignored = list(merged.get("ignore_columns") or [])
    for column in (local.get("ignore_columns") or []):
        if column not in ignored:
            ignored.append(column)
    if ignored:
        merged["ignore_columns"] = ignored
    return merged


def load_mapping(mapping_path: str) -> Dict[str, Any]:
    """读基础字典 + 本地覆盖（没有本地文件就只用基础）。"""
    base = _read_yaml(mapping_path)
    local = _read_yaml(local_path_for(mapping_path))
    return merge(base, local)


def append_aliases(local_path: str, updates: Dict[str, List[str]]) -> int:
    """把新别名追加到本地覆盖文件；返回新增条数（已存在的不重复写）。

    :param updates: ``{字段名: [新列名, ...]}``
    """
    clean: Dict[str, List[str]] = {}
    for fieldname, aliases in (updates or {}).items():
        values = [str(a).strip() for a in (aliases or []) if str(a).strip()]
        if values:
            clean[str(fieldname)] = values
    if not clean:
        return 0

    data = _read_yaml(local_path)
    columns = data.setdefault("columns", {})
    added = 0
    for fieldname, aliases in clean.items():
        target = columns.setdefault(fieldname, [])
        if not isinstance(target, list):
            target = columns[fieldname] = list(target) if target else []
        for alias in aliases:
            if alias not in target:
                target.append(alias)
                added += 1
    if not added:
        return 0

    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    with open(local_path, "w", encoding="utf-8") as f:
        f.write("# 本地列名覆盖 —— 由「从样例表对齐字段」自动追加，可随时删除/手工整理。\n"
                "# 基础字典在 import_mapping.yaml（带注释、人维护）；这里只有机器学到的别名。\n")
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False,
                       default_flow_style=False)
    return added


def _read_yaml(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


__all__ = ["append_aliases", "load_mapping", "local_path_for", "merge"]
