"""引擎词汇表的读取入口（配置在 ``config/engine_vocab.yaml``）。

为什么单独一层：引擎里**不写业务词**（学生姓名/年级/班级…），这些词是"某个录入
场景"的词汇，属于数据。``tests/test_architecture.py`` 会挡住把业务词写回引擎 .py。

读取失败一律返回空（引擎继续能跑，只是关键词匹配退化成直接用属性名）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(_ROOT, "config", "engine_vocab.yaml")

_CACHE: Dict[str, Any] = {}


def _load() -> Dict[str, Any]:
    global _CACHE
    if _CACHE:
        return _CACHE
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        _CACHE = data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        _CACHE = {}
    return _CACHE


def matcher_keywords() -> Dict[str, List[str]]:
    """页面字段名 → 属性名的关键词表。"""
    return dict(_load().get("matcher_keywords") or {})


def rule(key: str, default: Any = None) -> Any:
    """规则用的配置（字段名清单 / 正则 / 区间映射）。"""
    return (_load().get("rule") or {}).get(key, default)


def source_fields(source_name: str) -> List[str]:
    """某个数据源提供的字段清单。"""
    return list((_load().get("source_fields") or {}).get(source_name) or [])


def mock_data(name: str) -> Dict[str, Any]:
    """Mock 数据（无网络/演示用）。"""
    return dict((_load().get("mock") or {}).get(name) or {})


def scanner_mock_fields() -> List[dict]:
    """页面扫描的 Mock 字段。"""
    return list(_load().get("scanner_mock_fields") or [])


__all__ = ["matcher_keywords", "rule", "source_fields", "mock_data",
           "scanner_mock_fields"]
