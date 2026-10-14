"""批量投保的 SQLite 存储层（设计文档第四节的数据模型）。

表结构：batch / applicant / status_log / validation
约束：
  - 身份证号**加密入库**（见 :mod:`batch.crypto`），读出时解密
  - 状态变更一律走 :meth:`BatchDB.set_status`，同时写 status_log 审计
  - status_log 里**不记身份证号**，只记 applicant id

线程安全：GUI 的录入线程与界面线程会并发访问，这里用
``check_same_thread=False`` + 一把可重入锁串行化所有语句。
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import crypto
from .models import (ALL_STATUSES, STATUS_DATA_ERROR, STATUS_PENDING,
                     Applicant, Batch, StatusTransitionError, ValidationResult,
                     assert_transition)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "batch.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS batch (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL,
  source_file TEXT,
  product     TEXT DEFAULT '',
  total       INTEGER DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applicant (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  batch_id      INTEGER NOT NULL REFERENCES batch(id),
  name          TEXT NOT NULL,
  id_card       TEXT NOT NULL,
  phone         TEXT NOT NULL,
  payer_name    TEXT DEFAULT '',
  payer_id_card TEXT DEFAULT '',
  payer_phone   TEXT DEFAULT '',
  applicant_name TEXT DEFAULT '',
  school        TEXT NOT NULL,
  grade         TEXT DEFAULT '',
  class_name    TEXT NOT NULL,
  student_level TEXT DEFAULT '',
  period_start  TEXT NOT NULL,
  period_end    TEXT NOT NULL,
  status        TEXT NOT NULL,
  fail_reason   TEXT,
  screenshot    TEXT,
  source_image  TEXT DEFAULT '',
  retry_count   INTEGER DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS status_log (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  applicant_id INTEGER NOT NULL REFERENCES applicant(id),
  from_status  TEXT,
  to_status    TEXT NOT NULL,
  operator     TEXT NOT NULL,
  note         TEXT,
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  applicant_id INTEGER NOT NULL REFERENCES applicant(id),
  field        TEXT NOT NULL,
  rule         TEXT NOT NULL,
  passed       INTEGER NOT NULL,
  detail       TEXT
);

CREATE INDEX IF NOT EXISTS idx_applicant_status ON applicant(batch_id, status);
CREATE INDEX IF NOT EXISTS idx_applicant_idcard ON applicant(id_card);
CREATE INDEX IF NOT EXISTS idx_status_log_applicant ON status_log(applicant_id);
CREATE INDEX IF NOT EXISTS idx_validation_applicant ON validation(applicant_id);
"""

#: 新增过的列（旧库自动补列，保证 schema 演进不丢数据）。
_MIGRATIONS: Dict[str, Dict[str, str]] = {
    "batch": {
        "product": "TEXT DEFAULT ''",
    },
    "applicant": {
        "payer_name": "TEXT DEFAULT ''",
        "payer_id_card": "TEXT DEFAULT ''",
        "payer_phone": "TEXT DEFAULT ''",
        "applicant_name": "TEXT DEFAULT ''",
        "grade": "TEXT DEFAULT ''",
        "student_level": "TEXT DEFAULT ''",
        "source_image": "TEXT DEFAULT ''",
        "source_row": "INTEGER DEFAULT 0",
        "source_sheet": "TEXT DEFAULT ''",
    },
}

#: 需要加密存储的字段。
_SENSITIVE_FIELDS = ("id_card", "payer_id_card")

#: applicant 表中所有可写字段（update 白名单）。
_WRITABLE_FIELDS = (
    "name", "id_card", "phone",
    "payer_name", "payer_id_card", "payer_phone", "applicant_name",
    "school", "grade", "class_name", "student_level",
    "period_start", "period_end",
    "status", "fail_reason", "screenshot", "source_image", "retry_count",
    "source_row", "source_sheet",
)


def now_str() -> str:
    """本地时间戳字符串（秒级，人类可读且可排序）。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class BatchDB:
    """批量投保数据库（建表 / 查询 / 状态流转 / 审计）。"""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _DEFAULT_DB_PATH
        if self.path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    # ------------------------------------------------------------------
    # 建表 / 迁移
    # ------------------------------------------------------------------
    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(_SCHEMA)
            for table, columns in _MIGRATIONS.items():
                existing = {r["name"] for r in
                            self._conn.execute(f"PRAGMA table_info({table})")}
                for col, ddl in columns.items():
                    if col not in existing:
                        self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------
    # 批次
    # ------------------------------------------------------------------
    def create_batch(self, name: str, source_file: str = "", total: int = 0,
                     product: str = "") -> Batch:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO batch (name, source_file, total, created_at, product) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, source_file, int(total), now_str(), product))
            self._conn.commit()
            return self.get_batch(cur.lastrowid)

    def get_batch(self, batch_id: int) -> Optional[Batch]:
        row = self._query_one("SELECT * FROM batch WHERE id = ?", (batch_id,))
        return Batch.from_row(row) if row else None

    def list_batches(self) -> List[Batch]:
        rows = self._query("SELECT * FROM batch ORDER BY id DESC")
        return [Batch.from_row(r) for r in rows]

    def update_batch_total(self, batch_id: int) -> int:
        """按 applicant 实际条数刷新 batch.total，返回新值。"""
        row = self._query_one(
            "SELECT COUNT(*) AS n FROM applicant WHERE batch_id = ?", (batch_id,))
        total = int(row["n"]) if row else 0
        with self._lock:
            self._conn.execute("UPDATE batch SET total = ? WHERE id = ?", (total, batch_id))
            self._conn.commit()
        return total

    def delete_batch(self, batch_id: int) -> None:
        """删除批次及其明细/校验/审计（不可恢复，仅供测试与误导入回退）。"""
        with self._lock:
            self._conn.execute(
                "DELETE FROM validation WHERE applicant_id IN "
                "(SELECT id FROM applicant WHERE batch_id = ?)", (batch_id,))
            self._conn.execute(
                "DELETE FROM status_log WHERE applicant_id IN "
                "(SELECT id FROM applicant WHERE batch_id = ?)", (batch_id,))
            self._conn.execute("DELETE FROM applicant WHERE batch_id = ?", (batch_id,))
            self._conn.execute("DELETE FROM batch WHERE id = ?", (batch_id,))
            self._conn.commit()

    # ------------------------------------------------------------------
    # 明细：写入
    # ------------------------------------------------------------------
    def add_applicant(self, applicant: Applicant, operator: str = "system",
                      note: str = "导入") -> int:
        """新增一条明细（身份证号加密入库），并写首条状态审计。"""
        ts = now_str()
        values = {
            "batch_id": applicant.batch_id,
            "name": applicant.name,
            "id_card": applicant.id_card,
            "phone": applicant.phone,
            "payer_name": applicant.payer_name,
            "payer_id_card": applicant.payer_id_card,
            "payer_phone": applicant.payer_phone,
            "applicant_name": applicant.applicant_name,
            "school": applicant.school,
            "grade": applicant.grade,
            "class_name": applicant.class_name,
            "student_level": applicant.student_level,
            "period_start": applicant.period_start,
            "period_end": applicant.period_end,
            "status": applicant.status,
            "fail_reason": applicant.fail_reason,
            "screenshot": applicant.screenshot,
            "source_image": applicant.source_image,
            "source_row": int(getattr(applicant, "source_row", 0) or 0),
            "source_sheet": getattr(applicant, "source_sheet", "") or "",
            "retry_count": applicant.retry_count,
            "created_at": ts,
            "updated_at": ts,
        }
        for fieldname in _SENSITIVE_FIELDS:
            values[fieldname] = crypto.encrypt(values[fieldname] or "")

        columns = ", ".join(values.keys())
        marks = ", ".join("?" for _ in values)
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO applicant ({columns}) VALUES ({marks})",
                tuple(values.values()))
            applicant_id = cur.lastrowid
            self._conn.execute(
                "INSERT INTO status_log (applicant_id, from_status, to_status, "
                "operator, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (applicant_id, None, applicant.status, operator, note, ts))
            self._conn.commit()
        return applicant_id

    def update_fields(self, applicant_id: int, operator: str = "system",
                      note: str = "", **fields) -> None:
        """更新明细字段（不校验状态机；状态请走 :meth:`set_status`）。

        身份证号等敏感字段自动加密。
        """
        payload = {k: v for k, v in fields.items() if k in _WRITABLE_FIELDS}
        if not payload:
            return
        if "status" in payload:
            raise StatusTransitionError("状态变更请使用 set_status()（需写审计）")
        for fieldname in _SENSITIVE_FIELDS:
            if fieldname in payload:
                payload[fieldname] = crypto.encrypt(payload[fieldname] or "")
        payload["updated_at"] = now_str()

        assignments = ", ".join(f"{k} = ?" for k in payload)
        with self._lock:
            self._conn.execute(
                f"UPDATE applicant SET {assignments} WHERE id = ?",
                (*payload.values(), applicant_id))
            self._conn.commit()
        _ = (operator, note)  # 字段级修改暂不写审计（状态审计见 set_status）

    def save_applicant(self, applicant: Applicant, operator: str = "system",
                       note: str = "") -> None:
        """按数据类整体回写（用于校验/修正后覆盖）。"""
        fields: Dict[str, Any] = {k: getattr(applicant, k) for k in _WRITABLE_FIELDS
                                  if k != "status"}
        self.update_fields(applicant.id, operator=operator, note=note, **fields)

    def clear_sensitive(self, applicant_id: int, operator: str = "system") -> None:
        """把身份证号置为脱敏值（「用完即删」，录入成功后可用）。"""
        applicant = self.get_applicant(applicant_id)
        if applicant is None:
            return
        self.update_fields(
            applicant_id, operator=operator,
            id_card=crypto.mask_id_card(applicant.id_card),
            payer_id_card=crypto.mask_id_card(applicant.payer_id_card))
        with self._lock:
            self._conn.execute(
                "INSERT INTO status_log (applicant_id, from_status, to_status, "
                "operator, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (applicant_id, applicant.status, applicant.status, operator,
                 "清理敏感字段（身份证号脱敏）", now_str()))
            self._conn.commit()

    def clear_batch_sensitive(self, batch_id: int, operator: str = "system") -> int:
        """对整批执行脱敏，返回处理条数（已脱敏的跳过）。

        注意：id_card 在库里是 Fernet 密文（URL-safe base64，可能含 ``-``），
        **不能**用 SQL LIKE 在密文上判断是否已脱敏——必须解密后再判断，
        否则会随机漏处理（密文里恰好含 ``-`` 时）。
        """
        rows = self._query("SELECT id FROM applicant WHERE batch_id = ?", (batch_id,))
        count = 0
        for row in rows:
            applicant = self.get_applicant(row["id"], with_validations=False)
            if applicant is None or (crypto.is_masked(applicant.id_card)
                                     and crypto.is_masked(applicant.payer_id_card)):
                continue
            self.clear_sensitive(row["id"], operator=operator)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 明细：状态流转
    # ------------------------------------------------------------------
    def set_status(self, applicant_id: int, to_status: str,
                   operator: str = "system",
                   note: str = "",
                   fail_reason: Optional[str] = None) -> None:
        """设置状态：先校验迁移合法性，再写库 + 审计。

        :raises StatusTransitionError: 非法迁移
        """
        row = self._query_one("SELECT status FROM applicant WHERE id = ?", (applicant_id,))
        if row is None:
            raise ValueError(f"明细不存在：id={applicant_id}")
        from_status = row["status"]
        assert_transition(from_status, to_status)

        payload: Dict[str, Any] = {"status": to_status, "updated_at": now_str()}
        if fail_reason is not None:
            payload["fail_reason"] = fail_reason
        assignments = ", ".join(f"{k} = ?" for k in payload)
        ts = payload["updated_at"]
        with self._lock:
            self._conn.execute(
                f"UPDATE applicant SET {assignments} WHERE id = ?",
                (*payload.values(), applicant_id))
            self._conn.execute(
                "INSERT INTO status_log (applicant_id, from_status, to_status, "
                "operator, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (applicant_id, from_status, to_status, operator, note, ts))
            self._conn.commit()

    def reset_stuck_filling(self, batch_id: Optional[int] = None,
                            operator: str = "system") -> int:
        """中断恢复：把「录入中」的记录重置为「已确认」，返回条数。

        进程被杀后重启时调用，避免记录永久卡在「录入中」。
        """
        if batch_id is None:
            rows = self._query("SELECT id FROM applicant WHERE status = '录入中'")
        else:
            rows = self._query(
                "SELECT id FROM applicant WHERE status = '录入中' AND batch_id = ?",
                (batch_id,))
        for row in rows:
            self.set_status(row["id"], "已确认", operator=operator,
                            note="中断恢复：重置卡住的「录入中」记录")
        return len(rows)

    def status_history(self, applicant_id: int) -> List[dict]:
        """某条明细的状态变更审计（不包含身份证号）。"""
        rows = self._query(
            "SELECT * FROM status_log WHERE applicant_id = ? ORDER BY id", (applicant_id,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 明细：读取
    # ------------------------------------------------------------------
    def get_applicant(self, applicant_id: int,
                      with_validations: bool = True) -> Optional[Applicant]:
        row = self._query_one("SELECT * FROM applicant WHERE id = ?", (applicant_id,))
        if row is None:
            return None
        applicant = Applicant.from_row(row)
        self._decrypt(applicant)
        if with_validations:
            applicant.validations = self.list_validations(applicant_id)
        return applicant

    def list_applicants(self, batch_id: int,
                        statuses: Optional[Sequence[str]] = None,
                        limit: Optional[int] = None,
                        with_validations: bool = False) -> List[Applicant]:
        sql = "SELECT * FROM applicant WHERE batch_id = ?"
        params: List[Any] = [batch_id]
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            sql += f" AND status IN ({marks})"
            params.extend(statuses)
        sql += " ORDER BY id"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self._query(sql, tuple(params))

        applicants: List[Applicant] = []
        for row in rows:
            applicant = Applicant.from_row(row)
            self._decrypt(applicant)
            if with_validations:
                applicant.validations = self.list_validations(applicant.id)
            applicants.append(applicant)
        return applicants

    def count_by_status(self, batch_id: int) -> Dict[str, int]:
        """本批次各状态计数（含 0 的状态，便于界面直接展示）。"""
        rows = self._query(
            "SELECT status, COUNT(*) AS n FROM applicant WHERE batch_id = ? "
            "GROUP BY status", (batch_id,))
        counts = {status: 0 for status in ALL_STATUSES}
        for row in rows:
            counts[row["status"]] = int(row["n"])
        return counts

    def find_duplicates(self, batch_id: int) -> List[dict]:
        """同批次内「同一身份证号 + 同一保险期间」的重复投保组。

        身份证号在库里是密文，无法用 SQL 直接 GROUP BY，因此取出来
        解密后在内存里比对（单批次数据量在千级以内，成本可忽略）。
        """
        applicants = self.list_applicants(batch_id)
        groups: Dict[tuple, List[int]] = {}
        for applicant in applicants:
            key = (applicant.id_card, applicant.period_start, applicant.period_end)
            groups.setdefault(key, []).append(applicant.id)
        return [
            {"id_card": key[0], "period_start": key[1], "period_end": key[2],
             "applicant_ids": ids}
            for key, ids in groups.items() if len(ids) > 1
        ]

    # ------------------------------------------------------------------
    # 校验结果
    # ------------------------------------------------------------------
    def replace_validations(self, applicant_id: int,
                            results: Iterable[ValidationResult]) -> None:
        """整体替换某条明细的校验结果（重新校验时用）。"""
        with self._lock:
            self._conn.execute("DELETE FROM validation WHERE applicant_id = ?",
                               (applicant_id,))
            self._conn.executemany(
                "INSERT INTO validation (applicant_id, field, rule, passed, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                [(applicant_id, r.field, r.rule, 1 if r.passed else 0, r.detail)
                 for r in results])
            self._conn.commit()

    def list_validations(self, applicant_id: int) -> List[ValidationResult]:
        rows = self._query(
            "SELECT * FROM validation WHERE applicant_id = ? ORDER BY id",
            (applicant_id,))
        return [ValidationResult.from_row(r) for r in rows]

    def failed_validations(self, batch_id: int) -> List[dict]:
        """本批次所有未通过的校验项（复核界面的异常清单）。"""
        rows = self._query(
            "SELECT v.*, a.name AS applicant_name FROM validation v "
            "JOIN applicant a ON a.id = v.applicant_id "
            "WHERE a.batch_id = ? AND v.passed = 0 ORDER BY v.applicant_id, v.id",
            (batch_id,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _decrypt(self, applicant: Applicant) -> None:
        applicant.id_card = crypto.decrypt(applicant.id_card)
        applicant.payer_id_card = crypto.decrypt(applicant.payer_id_card)

    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()
