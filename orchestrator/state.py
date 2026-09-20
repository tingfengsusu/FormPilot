"""任务状态机（设计文档 4.2）。

    PENDING → OCR → MAPPING → FILLING → VERIFYING → DONE
                              │           │
                              └── FAILED ─┘ →（还有重试次数）→ PENDING
                                             重试耗尽 → ALERT

与业务状态机（batch/models.py 的 Applicant）分开：那个描述「一条投保记录
在业务上走到哪」，这个描述「一次消息任务的处理进度」。
"""
from __future__ import annotations

from typing import Dict, List, Optional

PENDING = "PENDING"        # 已入队，等 worker 取
OCR = "OCR"                # 正在识别附件（证件照 → 字段）
MAPPING = "MAPPING"        # 正在做字段映射（识别结果 → 品种属性）
FILLING = "FILLING"        # 正在填表（Playwright）
VERIFYING = "VERIFYING"    # 正在回读校验
DONE = "DONE"              # 完成（截图已回推）
FAILED = "FAILED"          # 失败（还有重试机会）
ALERT = "ALERT"            # 重试耗尽，需人工介入（设计文档的「异常预警」）

#: 执行中会出现的状态（用于「任务在跑」判断）。
WORKING_STATUSES: List[str] = [OCR, MAPPING, FILLING, VERIFYING]

#: 终态。
TERMINAL_STATUSES: List[str] = [DONE, ALERT]

#: 全部状态（界面/报表展示顺序）。
ALL_TASK_STATUSES: List[str] = [PENDING, OCR, MAPPING, FILLING, VERIFYING,
                                DONE, FAILED, ALERT]

#: 合法的状态迁移。
TASK_TRANSITIONS: Dict[str, List[str]] = {
    PENDING: [OCR, FAILED],
    OCR: [MAPPING, FAILED],
    MAPPING: [FILLING, FAILED],
    FILLING: [VERIFYING, FAILED],
    VERIFYING: [DONE, FAILED],
    FAILED: [PENDING, ALERT],       # 还有次数 → 回队列；用尽 → 告警
    DONE: [],
    ALERT: [PENDING],               # 人工处理后可重新入队
}

#: 兼容别名（旧代码里可能叫 FILLING_PENDING）。
FILLING_PENDING = FILLING


class TaskTransitionError(ValueError):
    """非法的任务状态迁移。"""


def can_task_transition(from_status: Optional[str], to_status: str) -> bool:
    """判断任务状态迁移是否合法（``from`` 为 None 表示新建）。"""
    if to_status not in ALL_TASK_STATUSES:
        return False
    if from_status is None:
        return to_status == PENDING
    if from_status == to_status:
        return True                  # 幂等重写
    return to_status in TASK_TRANSITIONS.get(from_status, [])


def assert_task_transition(from_status: Optional[str], to_status: str) -> None:
    if not can_task_transition(from_status, to_status):
        raise TaskTransitionError(f"非法任务状态迁移：{from_status} → {to_status}")


__all__ = ["PENDING", "OCR", "MAPPING", "FILLING", "VERIFYING", "DONE",
           "FAILED", "ALERT", "ALL_TASK_STATUSES", "TASK_TRANSITIONS",
           "WORKING_STATUSES", "TERMINAL_STATUSES", "FILLING_PENDING",
           "TaskTransitionError", "can_task_transition",
           "assert_task_transition"]
