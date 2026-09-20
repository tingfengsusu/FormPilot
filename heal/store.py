"""案例库（SQLite）+ 相似检索（MVP 实现，接口固定）。

**接口是契约**：``find_similar`` 返回 ``[(HealCase, similarity)]``。
P2 换成向量检索（复用 MemVault 的 ``VectorStore`` + ``get_text_embedder``，
混合检索再加 FTS5 + RRF 融合）时，只替换本文件的实现，
``healer`` / ``worker`` / CLI 都不用动。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .features import feature_similarity
from .models import (SOURCE_SEED, HealCase, HealStats, FailureFeature)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "gateway.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS heal_case (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  signature     TEXT NOT NULL,
  symptom       TEXT NOT NULL,
  step          TEXT DEFAULT '',
  variety       TEXT DEFAULT '',
  property      TEXT DEFAULT '',
  cause         TEXT DEFAULT '',
  remedy        TEXT NOT NULL,
  remedy_params TEXT DEFAULT '{}',
  source        TEXT DEFAULT 'seed',
  enabled       INTEGER DEFAULT 1,
  occurrences   INTEGER DEFAULT 0,
  hits          INTEGER DEFAULT 0,
  successes     INTEGER DEFAULT 0,
  last_seen_at  TEXT DEFAULT '',
  created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS heal_attempt (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    INTEGER NOT NULL,
  case_id    INTEGER,
  similarity REAL DEFAULT 0,
  remedy     TEXT DEFAULT '',
  applied    INTEGER DEFAULT 0,
  ok         INTEGER DEFAULT 0,
  settled    INTEGER DEFAULT 0,
  detail     TEXT DEFAULT '',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_heal_case_sig ON heal_case(signature);
CREATE INDEX IF NOT EXISTS idx_heal_attempt_task ON heal_attempt(task_id);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class HealStore:
    """故障案例库（线程安全：一把可重入锁串行化）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _DEFAULT_DB_PATH
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 案例读写
    # ------------------------------------------------------------------
    def upsert_case(self, case: HealCase) -> HealCase:
        """新增或按 (signature, remedy) 合并（用于种子重复加载、人工修正）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM heal_case WHERE signature = ? AND remedy = ?"
                " ORDER BY id LIMIT 1",
                (case.signature, case.remedy)).fetchone()
            if row is not None:
                # 已存在：更新描述性字段（症状/参数/根因），统计不动
                self._conn.execute(
                    "UPDATE heal_case SET symptom = ?, step = ?, variety = ?,"
                    " property = ?, cause = ?, remedy_params = ? WHERE id = ?",
                    (case.symptom, case.step, case.variety, case.property_name,
                     case.cause, json.dumps(case.remedy_params, ensure_ascii=False),
                     row["id"]))
                self._conn.commit()
                return self.get(int(row["id"]))
            ts = now_str()
            cur = self._conn.execute(
                "INSERT INTO heal_case (signature, symptom, step, variety,"
                " property, cause, remedy, remedy_params, source, enabled,"
                " occurrences, hits, successes, last_seen_at, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,0,0,0,'',?)",
                (case.signature, case.symptom, case.step, case.variety,
                 case.property_name, case.cause, case.remedy,
                 json.dumps(case.remedy_params or {}, ensure_ascii=False),
                 case.source or SOURCE_SEED, 1 if case.enabled else 0, ts))
            self._conn.commit()
            return self.get(cur.lastrowid)

    def get(self, case_id: int) -> Optional[HealCase]:
        row = self._query_one("SELECT * FROM heal_case WHERE id = ?", (case_id,))
        return _case_from_row(row) if row else None

    def list_cases(self, enabled_only: bool = False,
                   limit: int = 100) -> List[HealCase]:
        where = "WHERE enabled = 1" if enabled_only else ""
        rows = self._query(
            f"SELECT * FROM heal_case {where} ORDER BY hits DESC, id LIMIT"
            f" {int(limit)}")
        return [_case_from_row(r) for r in rows]

    def set_enabled(self, case_id: int, enabled: bool) -> None:
        """启用/禁用案例（误命中时人工处理）。"""
        with self._lock:
            self._conn.execute("UPDATE heal_case SET enabled = ? WHERE id = ?",
                               (1 if enabled else 0, case_id))
            self._conn.commit()

    # ------------------------------------------------------------------
    # 相似检索（MVP：token 重合 + 序列比率；P2 换向量）
    # ------------------------------------------------------------------
    def find_similar(self, feature: FailureFeature, top_k: int = 3,
                     threshold: float = 0.0) -> List[Tuple[HealCase, float]]:
        scored: List[Tuple[HealCase, float]] = []
        for case in self.list_cases(enabled_only=True):
            score = feature_similarity(feature, case)
            if score >= max(0.0, threshold):
                scored.append((case, score))
        scored.sort(key=lambda item: (-item[1], item[0].id or 0))
        return scored[:max(1, int(top_k))]

    # ------------------------------------------------------------------
    # 统计与留痕
    # ------------------------------------------------------------------
    def record_seen(self, case_id: int) -> None:
        """见过一次（命中或未命中都算，用于 occurrences）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE heal_case SET occurrences = occurrences + 1,"
                " last_seen_at = ? WHERE id = ?", (now_str(), case_id))
            self._conn.commit()

    def record_hit(self, case_id: int, success: bool) -> None:
        """命中一次（success = 处置后是否真的成功）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE heal_case SET hits = hits + 1,"
                " successes = successes + ?, last_seen_at = ? WHERE id = ?",
                (1 if success else 0, now_str(), case_id))
            self._conn.commit()

    def record_attempt(self, task_id: int, case_id: Optional[int],
                       similarity: float = 0.0, remedy: str = "",
                       applied: bool = False, ok: bool = False,
                       detail: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO heal_attempt (task_id, case_id, similarity, remedy,"
                " applied, ok, settled, detail, created_at)"
                " VALUES (?,?,?,?,?,?,0,?,?)",
                (task_id, case_id, float(similarity), remedy,
                 1 if applied else 0, 1 if ok else 0, detail[:500], now_str()))
            self._conn.commit()
            return int(cur.lastrowid)

    def settle_attempts(self, task_id: int, ok: bool) -> List[int]:
        """任务最终成功/失败时，结算这条任务上还没结算的自愈尝试。

        :return: 结算到的 case_id 列表（调用方用它更新案例命中率）
        """
        rows = self._query(
            "SELECT id, case_id FROM heal_attempt WHERE task_id = ?"
            " AND settled = 0 AND case_id IS NOT NULL", (task_id,))
        if not rows:
            return []
        with self._lock:
            self._conn.execute(
                "UPDATE heal_attempt SET settled = 1, ok = ? WHERE task_id = ?"
                " AND settled = 0", (1 if ok else 0, task_id))
            self._conn.commit()
        return [int(r["case_id"]) for r in rows]

    def attempts(self, task_id: Optional[int] = None,
                 day: str = "") -> List[Dict[str, Any]]:
        clauses, params = [], []
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if day:
            clauses.append("created_at LIKE ?")
            params.append(f"{day}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            f"SELECT * FROM heal_attempt {where} ORDER BY id DESC", tuple(params))
        return [dict(r) for r in rows]

    def stats(self, day: str = "") -> HealStats:
        where, params = "", ()
        if day:
            where, params = " WHERE created_at LIKE ?", (f"{day}%",)
        rows = self._query(f"SELECT * FROM heal_attempt{where}", params)
        report = HealStats(day=day or "")
        report.attempts = len(rows)
        report.applied = sum(1 for r in rows if r["applied"])
        report.succeeded = sum(1 for r in rows if r["applied"] and r["ok"])
        report.unmatched = sum(1 for r in rows if r["case_id"] is None)
        report.cases = len(self.list_cases())
        report.top_cases = [
            {"id": case.id, "hits": case.hits, "describe": case.describe()}
            for case in self.list_cases(limit=5) if case.hits]
        return report

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()


def _case_from_row(row: sqlite3.Row) -> HealCase:
    try:
        params = json.loads(row["remedy_params"] or "{}")
    except ValueError:
        params = {}
    return HealCase(
        id=int(row["id"]), signature=row["signature"] or "",
        symptom=row["symptom"] or "", step=row["step"] or "",
        variety=row["variety"] or "", property_name=row["property"] or "",
        cause=row["cause"] or "", remedy=row["remedy"] or "",
        remedy_params=params if isinstance(params, dict) else {},
        source=row["source"] or SOURCE_SEED, enabled=bool(row["enabled"]),
        occurrences=int(row["occurrences"] or 0), hits=int(row["hits"] or 0),
        successes=int(row["successes"] or 0),
        last_seen_at=row["last_seen_at"] or "", created_at=row["created_at"] or "")


__all__ = ["HealStore", "now_str"]
