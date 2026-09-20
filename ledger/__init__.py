"""台账（设计文档 4.4）：把执行结果沉淀成可量化的提效数据。

`ledger/export.py`（导出到飞书多维表格）属于可选扩展，暂未实现。
"""
from .db import DEFAULT_MANUAL_SECONDS, Ledger, LedgerEntry

__all__ = ["Ledger", "LedgerEntry", "DEFAULT_MANUAL_SECONDS"]
