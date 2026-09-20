"""SQLite 任务队列（设计文档 4.2 / 第七节的数据模型）。

表结构按设计文档：

- ``task``：一条消息 = 一个任务，``msg_id`` 唯一（幂等）
- ``task_step``：每一步一条（OCR/MAPPING/FILLING/VERIFYING），用于排查与报表
- ``ledger``：业务台账（提效数据来源，P1 用；这里先建表不写入）

单 worker 消费，所以出队就是「取最老的一条 PENDING 并置为 OCR」——
不需要分布式锁（设计文档明确说初期不用 Redis）。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from gateway.models import Attachment, InboundMessage

from . import state as st

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "gateway.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  msg_id          TEXT UNIQUE NOT NULL,   -- 平台消息 ID（幂等键）
  channel         TEXT NOT NULL,          -- feishu / wecom / console
  sender_id       TEXT DEFAULT '',
  chat_id         TEXT DEFAULT '',        -- 回推目标
  message_text    TEXT DEFAULT '',
  variety         TEXT DEFAULT '',        -- 从消息文本解析出的品种
  status          TEXT NOT NULL,
  retry_count     INTEGER DEFAULT 0,
  next_attempt_at TEXT DEFAULT '',        -- 退避：到点前不取
  attachments     TEXT DEFAULT '[]',      -- JSON: [{kind,file_key,name,path}]
  result          TEXT DEFAULT '',        -- 成功摘要 / 失败原因
  screenshot      TEXT DEFAULT '',
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_step (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    INTEGER NOT NULL REFERENCES task(id),
  step       TEXT NOT NULL,
  ok         INTEGER NOT NULL,
  detail     TEXT DEFAULT '',
  elapsed_ms INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ledger (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id        INTEGER NOT NULL REFERENCES task(id),
  applicant      TEXT DEFAULT '',
  variety        TEXT DEFAULT '',
  filled_fields  INTEGER DEFAULT 0,
  manual_seconds INTEGER DEFAULT 0,       -- 人工填写预估耗时（提效数据）
  auto_seconds   INTEGER DEFAULT 0,       -- 自动耗时
  created_at     TEXT NOT NULL
);

-- 人工指令信箱：执行线程在「等签名/等支付」时轮询它（见 executor/run.py）
CREATE TABLE IF NOT EXISTS session_cmd (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id    INTEGER NOT NULL,
  action     TEXT NOT NULL,               -- sign_done / paid / cancel
  consumed   INTEGER DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_status ON task(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_task_step   ON task_step(task_id);
CREATE INDEX IF NOT EXISTS idx_session_cmd ON session_cmd(task_id, consumed);
"""


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Task:
    """一个消息任务。"""

    id: Optional[int] = None
    msg_id: str = ""
    channel: str = ""
    sender_id: str = ""
    chat_id: str = ""
    message_text: str = ""
    variety: str = ""
    status: str = st.PENDING
    retry_count: int = 0
    next_attempt_at: str = ""
    attachments: List[Attachment] = field(default_factory=list)
    result: str = ""
    screenshot: str = ""
    created_at: str = ""
    updated_at: str = ""

    @property
    def message(self) -> InboundMessage:
        """还原成统一消息模型（executor 用）。"""
        return InboundMessage(
            channel=self.channel, msg_id=self.msg_id, sender_id=self.sender_id,
            chat_id=self.chat_id, text=self.message_text,
            message_type="image" if any(a.is_image for a in self.attachments)
            else "text",
            attachments=list(self.attachments), received_at=self.created_at)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Task":
        try:
            raw = json.loads(row["attachments"] or "[]")
        except ValueError:
            raw = []
        keys = set(row.keys())
        return cls(
            id=row["id"], msg_id=row["msg_id"], channel=row["channel"],
            sender_id=row["sender_id"] or "",
            chat_id=(row["chat_id"] or "") if "chat_id" in keys else "",
            message_text=(row["message_text"] or "") if "message_text" in keys else "",
            variety=row["variety"] or "", status=row["status"],
            retry_count=row["retry_count"] or 0,
            next_attempt_at=(row["next_attempt_at"] or "")
            if "next_attempt_at" in keys else "",
            attachments=[Attachment.from_dict(a) for a in raw],
            result=row["result"] or "",
            screenshot=(row["screenshot"] or "") if "screenshot" in keys else "",
            created_at=row["created_at"] or "", updated_at=row["updated_at"] or "")


class TaskQueue:
    """任务队列（SQLite）。线程安全：一把可重入锁串行化所有语句。"""

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
    # 入队（幂等）
    # ------------------------------------------------------------------
    def enqueue(self, message: InboundMessage, variety: str = "") -> Optional[Task]:
        """消息入队；同一个 ``msg_id`` 重复入队直接返回 None（幂等）。"""
        with self._lock:
            exist = self._conn.execute(
                "SELECT * FROM task WHERE msg_id = ?", (message.msg_id,)).fetchone()
            if exist is not None:
                return None
            ts = now_str()
            cur = self._conn.execute(
                "INSERT INTO task (msg_id, channel, sender_id, chat_id,"
                " message_text, variety, status, retry_count, next_attempt_at,"
                " attachments, result, screenshot, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,0,'',?,'','',?,?)",
                (message.msg_id, message.channel, message.sender_id,
                 message.chat_id, message.text, variety, st.PENDING,
                 json.dumps([a.to_dict() for a in message.attachments],
                            ensure_ascii=False), ts, ts))
            self._conn.commit()
            return self.get(cur.lastrowid)

    # ------------------------------------------------------------------
    # 取任务 / 状态流转
    # ------------------------------------------------------------------
    def claim_next(self) -> Optional[Task]:
        """取一条 PENDING（到点且未超重试上限）并置为 OCR（= 已被 worker 接管）。"""
        ts = now_str()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM task WHERE status = ? AND (next_attempt_at = ''"
                " OR next_attempt_at <= ?) ORDER BY id LIMIT 1",
                (st.PENDING, ts)).fetchone()
            if row is None:
                return None
            task = Task.from_row(row)
            self.set_status(task.id, st.OCR, note="worker 接管")
            return self.get(task.id)

    def set_status(self, task_id: int, to_status: str, note: str = "",
                   result: str = "", screenshot: str = "",
                   next_attempt_at: Optional[str] = None) -> None:
        """更新状态（校验迁移合法性）+ 写审计。

        ``next_attempt_at`` 不传时**保持原值**——否则会把退避时间清成空，
        导致刚失败的任务立刻被重新取走（实测踩过）。
        """
        row = self._query_one("SELECT status FROM task WHERE id = ?", (task_id,))
        if row is None:
            raise ValueError(f"任务不存在：id={task_id}")
        st.assert_task_transition(row["status"], to_status)
        payload: Dict[str, Any] = {"status": to_status, "updated_at": now_str()}
        if result:
            payload["result"] = result
        if screenshot:
            payload["screenshot"] = screenshot
        if next_attempt_at is not None:
            payload["next_attempt_at"] = next_attempt_at
        assignments = ", ".join(f"{k} = ?" for k in payload)
        with self._lock:
            self._conn.execute(f"UPDATE task SET {assignments} WHERE id = ?",
                               (*payload.values(), task_id))
            self._conn.commit()
        if note:
            self.add_step(task_id, step=to_status, ok=True, detail=note)

    def schedule_retry(self, task_id: int, delay_seconds: float,
                       reason: str) -> None:
        """失败后回队列（指数退避）：FAILED → PENDING（带下次可处理时间）。"""
        next_at = (datetime.now() + timedelta(seconds=max(0.0, delay_seconds))
                   ).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            self._conn.execute(
                "UPDATE task SET retry_count = retry_count + 1, updated_at = ?"
                " WHERE id = ?", (now_str(), task_id))
            self._conn.commit()
        self.set_status(task_id, st.FAILED, note=f"失败，{delay_seconds:.0f}s 后重试",
                        result=reason[:500])
        self.set_status(task_id, st.PENDING, note=f"{delay_seconds:.0f}s 后重新入队",
                        next_attempt_at=next_at)

    # ------------------------------------------------------------------
    # 步骤与查询
    # ------------------------------------------------------------------
    def add_step(self, task_id: int, step: str, ok: bool, detail: str = "",
                 elapsed_ms: int = 0) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO task_step (task_id, step, ok, detail, elapsed_ms,"
                " created_at) VALUES (?,?,?,?,?,?)",
                (task_id, step, 1 if ok else 0, detail[:1000], int(elapsed_ms),
                 now_str()))
            self._conn.commit()

    def steps(self, task_id: int) -> List[Dict[str, Any]]:
        rows = self._query("SELECT * FROM task_step WHERE task_id = ? ORDER BY id",
                           (task_id,))
        return [dict(r) for r in rows]

    def get(self, task_id: int) -> Optional[Task]:
        row = self._query_one("SELECT * FROM task WHERE id = ?", (task_id,))
        return Task.from_row(row) if row else None

    def get_by_msg_id(self, msg_id: str) -> Optional[Task]:
        row = self._query_one("SELECT * FROM task WHERE msg_id = ?", (msg_id,))
        return Task.from_row(row) if row else None

    def list_tasks(self, statuses: Optional[List[str]] = None,
                   limit: int = 50, day: str = "") -> List[Task]:
        """按状态/日期取任务（``day`` = ``YYYY-MM-DD``，按创建时间过滤）。"""
        clauses: List[str] = []
        params: List[Any] = []
        if statuses:
            marks = ", ".join("?" for _ in statuses)
            clauses.append(f"status IN ({marks})")
            params.extend(statuses)
        if day:
            clauses.append("created_at LIKE ?")
            params.append(f"{day}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            f"SELECT * FROM task {where} ORDER BY id DESC LIMIT {int(limit)}",
            tuple(params))
        return [Task.from_row(r) for r in rows]

    def count_by_status(self) -> Dict[str, int]:
        rows = self._query("SELECT status, COUNT(*) AS n FROM task GROUP BY status")
        counts = {status: 0 for status in st.ALL_TASK_STATUSES}
        for row in rows:
            counts[row["status"]] = int(row["n"])
        return counts

    def pending_count(self) -> int:
        row = self._query_one("SELECT COUNT(*) AS n FROM task WHERE status = ?",
                              (st.PENDING,))
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    # 人工指令信箱（执行线程等签名/等支付时用）
    # ------------------------------------------------------------------
    def send_command(self, task_id: int, action: str) -> None:
        """人工发一条指令给正在等待的执行线程（sign_done / paid / cancel）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO session_cmd (task_id, action, consumed, created_at)"
                " VALUES (?, ?, 0, ?)", (int(task_id), str(action), now_str()))
            self._conn.commit()

    def take_commands(self, task_id: int) -> List[str]:
        """取走该任务未消费的指令（取走即标记已消费）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, action FROM session_cmd WHERE task_id = ? AND"
                " consumed = 0 ORDER BY id", (int(task_id),)).fetchall()
            if rows:
                self._conn.executemany(
                    "UPDATE session_cmd SET consumed = 1 WHERE id = ?",
                    [(row["id"],) for row in rows])
                self._conn.commit()
        return [row["action"] for row in rows]

    # ------------------------------------------------------------------
    def _query(self, sql: str, params: tuple = ()) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()


__all__ = ["Task", "TaskQueue", "now_str"]
