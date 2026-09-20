"""单 worker 消费循环（设计文档 4.2）。

一条任务的完整处理：

    claim（PENDING → OCR）
      → execute(task, note_step)：内部按 OCR → MAPPING → FILLING → VERIFYING 推进
      → 成功：DONE + 回推「已填完 + 截图」
      → 失败：还有重试次数 → 指数退避回 PENDING；用尽 → ALERT + 回推失败原因

设计取舍（面试也会被问到，设计文档第十节给了口径）：
- **单 worker、串行**：投保系统一个会话一个操作，并发容易出问题；
  初期用 SQLite 队列而不是 Redis / 消息中间件
- **重试有上限**：3 次（指数退避），耗尽了转 ALERT 通知人，
  不做无限重试把站点打爆
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from gateway.models import OutboundResult

from . import state as st
from .queue import Task, TaskQueue

#: 执行器的返回：ok / 摘要 / 截图 / 失败原因。
@dataclass
class ExecResult:
    ok: bool = False
    summary: str = ""
    screenshot: str = ""
    error: str = ""

    @property
    def text(self) -> str:
        return self.summary if self.ok else (self.error or "处理失败")


#: 执行器签名：``executor(task, note_step) -> ExecResult``
#: ``note_step(step, ok, detail, elapsed_ms)`` 由 worker 提供，用来写 task_step
Executor = Callable[[Task, Callable[..., None]], ExecResult]


@dataclass
class WorkerReport:
    """一轮消费的结果（便于 CLI / 测试断言）。"""

    processed: int = 0
    done: int = 0
    retried: int = 0
    alerted: int = 0
    tasks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        if not self.processed:
            return "没有待处理任务"
        return (f"处理 {self.processed} 条：完成 {self.done}，"
                f"重试 {self.retried}，转告警 {self.alerted}")


class Worker:
    """把队列里的任务一条条交给执行器，并把结果回推给消息端。"""

    def __init__(self, queue: TaskQueue, executor: Executor,
                 channel: Any = None, max_retries: int = 3,
                 retry_backoff_seconds: float = 5.0,
                 sleep: Optional[Callable[[float], None]] = None):
        self.queue = queue
        self.executor = executor
        self.channel = channel
        self.max_retries = int(max_retries)
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        self._sleep = sleep or time.sleep
        self._stop = False

    # ------------------------------------------------------------------
    def run_once(self) -> Optional[Dict[str, Any]]:
        """处理一条任务（没有则返回 None）。"""
        task = self.queue.claim_next()
        if task is None:
            return None
        if self.channel is not None:
            self._reply(task, OutboundResult(
                ok=True, text=f"收到，正在处理（品种：{task.variety or '未识别'}）",
                task_id=task.id))
        started = time.time()
        try:
            result = self.executor(task, self._step_recorder(task))
        except Exception as e:  # noqa: BLE001 — 执行器异常不能把 worker 打死
            result = ExecResult(ok=False, error=f"执行异常：{e}")
        elapsed = time.time() - started
        record: Dict[str, Any] = {"task_id": task.id, "elapsed": elapsed}

        if result.ok:
            self.queue.set_status(task.id, st.DONE,
                                  note=f"完成：{result.summary or '已填写'}",
                                  result=result.summary, screenshot=result.screenshot)
            if self.channel is not None:
                self._reply(task, OutboundResult(
                    ok=True, text=f"{result.summary or '已填完'}"
                                  f"（{elapsed:.1f}s）请确认后提交",
                    screenshot=result.screenshot, task_id=task.id))
            record["status"] = st.DONE
            return record

        failed = self.queue.get(task.id)
        if failed is not None and failed.retry_count < self.max_retries:
            delay = self.retry_backoff_seconds * (2 ** failed.retry_count)
            self.queue.schedule_retry(task.id, delay, result.error)
            record["status"] = st.FAILED
            record["retry_in"] = delay
        else:
            # 重试耗尽 → 先落 FAILED 再转 ALERT（状态机只允许 FAILED → ALERT）
            self.queue.set_status(task.id, st.FAILED,
                                  note=f"重试耗尽：{result.error}",
                                  result=result.error)
            self.queue.set_status(task.id, st.ALERT,
                                  note=f"重试耗尽：{result.error}",
                                  result=result.error)
            if self.channel is not None:
                self._reply(task, OutboundResult(
                    ok=False, text=f"处理失败（已重试 {self.max_retries} 次）："
                                   f"{result.error}——需要人工介入",
                    task_id=task.id))
            record["status"] = st.ALERT
        return record

    def run_forever(self, poll_seconds: float = 1.0,
                    max_tasks: Optional[int] = None) -> WorkerReport:
        """持续消费（``max_tasks`` 到达后返回，便于演示/测试）。"""
        report = WorkerReport()
        self._stop = False
        while not self._stop:
            record = self.run_once()
            if record is None:
                if max_tasks is not None and report.processed >= max_tasks:
                    break
                self._sleep(poll_seconds)
                continue
            report.processed += 1
            status = record.get("status")
            if status == st.DONE:
                report.done += 1
            elif status == st.FAILED:
                report.retried += 1
            elif status == st.ALERT:
                report.alerted += 1
            report.tasks.append(record)
            if max_tasks is not None and report.processed >= max_tasks:
                break
        return report

    def stop(self) -> None:
        self._stop = True

    # ------------------------------------------------------------------
    def drain(self, max_tasks: Optional[int] = None,
              max_wait_seconds: float = 60.0) -> WorkerReport:
        """把当前队列里的任务处理干净（含重试退避：会等到下次可处理时间）。

        :param max_wait_seconds: 最多为退避等待多久（避免演示/测试挂太久）
        """
        report = WorkerReport()
        waited = 0.0
        while True:
            record = self.run_once()
            if record is None:
                # 队列空了：若还有 PENDING（在退避里），等一会儿再看
                if self.queue.pending_count() and waited < max_wait_seconds:
                    self._sleep(1.0)
                    waited += 1.0
                    continue
                break
            waited = 0.0
            report.processed += 1
            status = record.get("status")
            if status == st.DONE:
                report.done += 1
            elif status == st.FAILED:
                report.retried += 1
            elif status == st.ALERT:
                report.alerted += 1
            report.tasks.append(record)
            if max_tasks is not None and report.processed >= max_tasks:
                break
        return report

    # ------------------------------------------------------------------
    def _step_recorder(self, task: Task) -> Callable[..., None]:
        def note_step(step: str, ok: bool = True, detail: str = "",
                      elapsed_ms: int = 0) -> None:
            self.queue.add_step(task.id, step=step, ok=ok, detail=detail,
                                elapsed_ms=elapsed_ms)
            # 状态跟着步骤走（executor 只需报步骤，不用自己管状态迁移）
            target = {"OCR": st.OCR, "MAPPING": st.MAPPING,
                      "FILLING": st.FILLING, "VERIFYING": st.VERIFYING}.get(
                          str(step).upper())
            if not ok or target is None:
                return
            current = self.queue.get(task.id)
            if current is None:
                return
            if st.can_task_transition(current.status, target):
                self.queue.set_status(task.id, target, note=f"{step} 完成")
        return note_step

    def _reply(self, task: Task, result: OutboundResult) -> None:
        chat_id = task.chat_id or task.sender_id
        if not chat_id:
            return
        try:
            self.channel.reply(chat_id, result)
        except Exception as e:  # noqa: BLE001 — 回推失败不影响任务状态
            print(f"[worker] 回推失败：{e}")


__all__ = ["Worker", "WorkerReport", "ExecResult", "Executor"]
