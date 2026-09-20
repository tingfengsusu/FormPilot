"""台账：把「一次执行」变成可量化的一行数据（设计文档 4.4 / 第七节）。

**为什么必须有台账**：没有它就没有提效数据，没有数据就写不出业绩。
设计文档特别强调 `manual_seconds` / `auto_seconds` 这两列——

    平均每单节省 = manual_seconds - auto_seconds
    本月节省小时 = Σ(manual_seconds - auto_seconds) / 3600

表结构与设计文档一致（``ledger`` 表建在 gateway 库里，见
:mod:`orchestrator.queue` 的 schema），本模块只负责写入与统计。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "gateway.db")

#: 人工逐字段录入一条的默认预估耗时（秒）——提效对比的基准。
DEFAULT_MANUAL_SECONDS = 240


@dataclass
class LedgerEntry:
    """台账里的一行。"""

    task_id: int = 0
    applicant: str = ""
    variety: str = ""
    filled_fields: int = 0
    manual_seconds: int = DEFAULT_MANUAL_SECONDS
    auto_seconds: int = 0
    created_at: str = ""

    @property
    def saved_seconds(self) -> int:
        return max(0, int(self.manual_seconds) - int(self.auto_seconds))


class Ledger:
    """台账读写（同一个 SQLite 库，线程安全）。"""

    def __init__(self, path: Optional[str] = None,
                 manual_seconds: int = DEFAULT_MANUAL_SECONDS):
        self.path = path or _DEFAULT_DB_PATH
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self.manual_seconds = int(manual_seconds)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    def record(self, task_id: int, applicant: str = "", variety: str = "",
               filled_fields: int = 0, auto_seconds: float = 0,
               manual_seconds: Optional[int] = None) -> LedgerEntry:
        """写一条台账（同一个 task 只写一次——重复调用返回已有记录）。"""
        with self._lock:
            exist = self._conn.execute(
                "SELECT * FROM ledger WHERE task_id = ? ORDER BY id LIMIT 1",
                (task_id,)).fetchone()
            if exist is not None:
                return _entry_from_row(exist)
            entry = LedgerEntry(
                task_id=task_id, applicant=applicant, variety=variety,
                filled_fields=int(filled_fields),
                manual_seconds=int(manual_seconds or self.manual_seconds),
                auto_seconds=int(auto_seconds),
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            self._conn.execute(
                "INSERT INTO ledger (task_id, applicant, variety, filled_fields,"
                " manual_seconds, auto_seconds, created_at) VALUES (?,?,?,?,?,?,?)",
                (entry.task_id, entry.applicant, entry.variety,
                 entry.filled_fields, entry.manual_seconds, entry.auto_seconds,
                 entry.created_at))
            self._conn.commit()
            return entry

    # ------------------------------------------------------------------
    def summary(self, day: str = "") -> Dict[str, Any]:
        """台账汇总（默认今天）：条数 / 填写字段数 / 平均耗时 / 节省时间。

        :param day: ``YYYY-MM-DD``；空串表示全部
        """
        where, params = "", ()
        if day:
            where, params = " WHERE created_at LIKE ?", (f"{day}%",)
        rows = self._query(f"SELECT * FROM ledger{where} ORDER BY id", params)
        entries = [_entry_from_row(r) for r in rows]
        if not entries:
            return {"day": day or "全部", "count": 0, "filled_fields": 0,
                    "avg_auto_seconds": 0.0, "manual_seconds": self.manual_seconds,
                    "saved_seconds": 0, "saved_hours": 0.0, "entries": []}
        saved = sum(e.saved_seconds for e in entries)
        return {
            "day": day or "全部",
            "count": len(entries),
            "filled_fields": sum(e.filled_fields for e in entries),
            "avg_auto_seconds": round(
                sum(e.auto_seconds for e in entries) / len(entries), 1),
            "manual_seconds": self.manual_seconds,
            "saved_seconds": saved,
            "saved_hours": round(saved / 3600.0, 2),
            "entries": entries,
        }

    def by_variety(self, day: str = "") -> List[Dict[str, Any]]:
        """按品种统计（报表里的分布）。"""
        where, params = "", ()
        if day:
            where, params = " WHERE created_at LIKE ?", (f"{day}%",)
        rows = self._query(
            f"SELECT variety, COUNT(*) AS n, SUM(filled_fields) AS fields,"
            f" AVG(auto_seconds) AS avg_auto FROM ledger{where}"
            f" GROUP BY variety ORDER BY n DESC", params)
        return [{"variety": r["variety"] or "(未填)",
                 "count": int(r["n"] or 0),
                 "filled_fields": int(r["fields"] or 0),
                 "avg_auto_seconds": round(float(r["avg_auto"] or 0), 1)}
                for r in rows]

    def list_entries(self, limit: int = 20) -> List[LedgerEntry]:
        rows = self._query(f"SELECT * FROM ledger ORDER BY id DESC LIMIT {int(limit)}")
        return [_entry_from_row(r) for r in rows]

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()


def _entry_from_row(row: sqlite3.Row) -> LedgerEntry:
    return LedgerEntry(
        task_id=int(row["task_id"]), applicant=row["applicant"] or "",
        variety=row["variety"] or "", filled_fields=int(row["filled_fields"] or 0),
        manual_seconds=int(row["manual_seconds"] or 0),
        auto_seconds=int(row["auto_seconds"] or 0),
        created_at=row["created_at"] or "")


__all__ = ["Ledger", "LedgerEntry", "DEFAULT_MANUAL_SECONDS"]
