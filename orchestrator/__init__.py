"""编排层：SQLite 任务队列 + 单 worker + 重试（设计文档 4.2）。

    PENDING → OCR → MAPPING → FILLING → VERIFYING → DONE
                                  │           │
                                  └── FAILED ─┘ →（重试 <3）→ PENDING
                                                 重试耗尽 → ALERT

两个关键性质：
- **幂等**：``msg_id`` 唯一索引，平台重推的同一条消息只入队一次
- **可恢复**：状态与步骤都落库，进程重启后未完成的任务继续跑
"""
from .queue import TaskQueue
from .state import (ALL_TASK_STATUSES, ALERT, DONE, FAILED, FILLING_PENDING,
                    MAPPING, OCR, TASK_TRANSITIONS, VERIFYING,
                    TaskTransitionError, assert_task_transition,
                    can_task_transition)
from .worker import Worker, WorkerReport

__all__ = ["TaskQueue", "Worker", "WorkerReport", "ALL_TASK_STATUSES",
           "TASK_TRANSITIONS", "PENDING", "OCR", "MAPPING", "FILLING_PENDING",
           "VERIFYING", "DONE", "FAILED", "ALERT", "TaskTransitionError",
           "assert_task_transition", "can_task_transition"]
