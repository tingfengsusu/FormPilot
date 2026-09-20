"""orchestrator 测试（任务状态机 + 队列 + worker；不联网、不起浏览器）。

覆盖：
  - 状态机：合法/非法迁移，FAILED → ALERT，ALERT → PENDING（人工处理后重入队）
  - 队列：msg_id 幂等、出队即接管、步骤留痕、**退避时间不被覆盖**
    （实测踩过：set_status 把 next_attempt_at 清空，导致立刻重试）
  - worker：成功 → DONE + 回推；失败 → 退避重试；重试耗尽 → ALERT + 回推失败；
    执行器抛异常不能让 worker 崩
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta

from gateway.console import ConsoleChannel
from gateway.models import Attachment, InboundMessage, OutboundResult
from orchestrator import state as st
from orchestrator.queue import TaskQueue
from orchestrator.worker import ExecResult, Worker


@contextlib.contextmanager
def _queue(tmp_dir: str):
    queue = TaskQueue(os.path.join(tmp_dir, "gateway.db"))
    try:
        yield queue
    finally:
        queue.close()


def _message(msg_id: str = "om_1", text: str = "学平险",
             with_image: bool = True) -> InboundMessage:
    return InboundMessage(
        channel="console", msg_id=msg_id, sender_id="u1", chat_id="c1",
        text=text, message_type="image" if with_image else "text",
        attachments=[Attachment(kind="image", file_key="k", path="x.png")]
        if with_image else [])


# ----------------------------------------------------------------------
# 状态机
# ----------------------------------------------------------------------
def test_task_state_machine():
    assert st.can_task_transition(None, st.PENDING)
    assert st.can_task_transition(st.PENDING, st.OCR)
    assert st.can_task_transition(st.OCR, st.MAPPING)
    assert st.can_task_transition(st.MAPPING, st.FILLING)
    assert st.can_task_transition(st.FILLING, st.VERIFYING)
    assert st.can_task_transition(st.VERIFYING, st.DONE)
    assert st.can_task_transition(st.FAILED, st.PENDING)     # 还有重试次数
    assert st.can_task_transition(st.FAILED, st.ALERT)       # 重试耗尽
    assert st.can_task_transition(st.ALERT, st.PENDING)      # 人工处理后重入队
    # 非法：不能跳过步骤，终态不再流转
    assert not st.can_task_transition(st.PENDING, st.DONE)
    assert not st.can_task_transition(st.OCR, st.ALERT)      # 必须先落 FAILED
    assert not st.can_task_transition(st.DONE, st.PENDING)
    try:
        st.assert_task_transition(st.DONE, st.PENDING)
        assert False, "非法迁移应抛错"
    except st.TaskTransitionError:
        pass


# ----------------------------------------------------------------------
# 队列
# ----------------------------------------------------------------------
def test_queue_enqueue_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            first = queue.enqueue(_message("om_same"), variety="学平险")
            again = queue.enqueue(_message("om_same"), variety="学平险")
            assert first is not None and again is None       # 平台重推只入队一次
            assert queue.count_by_status()[st.PENDING] == 1
            # 附件也落库了（重启后能继续处理）
            stored = queue.get(first.id)
            assert stored.attachments and stored.attachments[0].path == "x.png"
            assert stored.message.text == "学平险"


def test_queue_claim_and_steps():
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            task = queue.enqueue(_message("om_1"))
            claimed = queue.claim_next()
            assert claimed.id == task.id
            assert claimed.status == st.OCR                  # 出队即接管
            assert queue.claim_next() is None                # 同一条不会被取两次
            queue.add_step(task.id, "OCR", ok=True, detail="识别 6 字段",
                           elapsed_ms=120)
            steps = queue.steps(task.id)
            assert any(s["step"] == "OCR" and s["elapsed_ms"] == 120
                       for s in steps)


def test_queue_retry_backoff_is_kept():
    """退避时间必须保留（实测踩过：被 set_status 清空 → 立刻重试）。"""
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            task = queue.enqueue(_message("om_2"))
            queue.claim_next()
            queue.schedule_retry(task.id, delay_seconds=60, reason="填表失败")
            stored = queue.get(task.id)
            assert stored.status == st.PENDING
            assert stored.retry_count == 1
            assert stored.next_attempt_at                    # 关键：退避没被清空
            assert stored.result.startswith("填表失败")
            # 还没到点 → 不取；把时间改成过去 → 能取到
            assert queue.claim_next() is None
            with queue._lock:                                # noqa: SLF001 — 测试内部
                queue._conn.execute(                      # noqa: SLF001
                    "UPDATE task SET next_attempt_at = ? WHERE id = ?",
                    ((datetime.now() - timedelta(seconds=5)
                      ).strftime("%Y-%m-%d %H:%M:%S"), task.id))
                queue._conn.commit()                      # noqa: SLF001
            assert queue.claim_next() is not None


def test_queue_status_counts_and_listing():
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            first = queue.enqueue(_message("om_a"))
            queue.enqueue(_message("om_b"))
            queue.claim_next()
            counts = queue.count_by_status()
            assert counts[st.OCR] == 1 and counts[st.PENDING] == 1
            assert counts[st.DONE] == 0
            assert queue.pending_count() == 1
            assert [t.id for t in queue.list_tasks(limit=5)] == [2, first.id]


# ----------------------------------------------------------------------
# worker
# ----------------------------------------------------------------------
class _FakeExecutor:
    """可控执行器：按脚本返回结果，并记录收到的步骤回调。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.steps = []

    def __call__(self, task, note_step):
        self.calls += 1
        note_step("OCR", ok=True, detail="识别 6 字段")
        note_step("MAPPING", ok=True, detail="映射 2 属性")
        result = self.results[min(self.calls - 1, len(self.results) - 1)]
        if isinstance(result, Exception):
            raise result
        if result.ok:
            note_step("FILLING", ok=True, detail="填写 5 字段")
            note_step("VERIFYING", ok=True, detail="回读 0 差异")
        else:
            note_step("FILLING", ok=False, detail=result.error)
        self.steps.append(task.id)
        return result


def test_worker_success_path_replies():
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            queue.enqueue(_message("om_ok"))
            channel = ConsoleChannel(printer=lambda *_: None)
            executor = _FakeExecutor([ExecResult(ok=True, summary="已填完 5 个字段",
                                                screenshot="shot.png")])
            worker = Worker(queue, executor, channel, max_retries=3,
                            retry_backoff_seconds=0, sleep=lambda _s: None)
            report = worker.drain()

            assert report.done == 1 and report.alerted == 0
            task = queue.list_tasks(limit=1)[0]
            assert task.status == st.DONE
            assert task.screenshot == "shot.png"
            # 步骤留痕：OCR/MAPPING/FILLING/VERIFYING + 状态迁移
            steps = [s["step"] for s in queue.steps(task.id)]
            for expected in ("OCR", "MAPPING", "FILLING", "VERIFYING", "DONE"):
                assert expected in steps, steps
            # 回推两条：受理 + 完成（截图存在才发图，这里不存在只有文本）
            texts = [item["text"] for item in channel.sent]
            assert any("正在处理" in t for t in texts)
            assert any("已填完" in t for t in texts)


def test_worker_retries_then_alerts():
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            queue.enqueue(_message("om_fail"))
            channel = ConsoleChannel(printer=lambda *_: None)
            executor = _FakeExecutor([ExecResult(ok=False, error="页面打不开")])
            worker = Worker(queue, executor, channel, max_retries=2,
                            retry_backoff_seconds=0, sleep=lambda _s: None)
            report = worker.drain()

            assert executor.calls == 3            # 1 次 + 重试 2 次
            assert report.alerted == 1
            task = queue.list_tasks(limit=1)[0]
            assert task.status == st.ALERT
            assert task.retry_count == 2
            assert "页面打不开" in task.result
            assert any("需要人工介入" in item["text"] for item in channel.sent)


def test_worker_recovers_after_retry():
    """第一次失败、第二次成功 → 任务最终 DONE（重试真的有用）。"""
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            queue.enqueue(_message("om_retry"))
            executor = _FakeExecutor([
                ExecResult(ok=False, error="偶发超时"),
                ExecResult(ok=True, summary="已填完"),
            ])
            worker = Worker(queue, executor, max_retries=3,
                            retry_backoff_seconds=0, sleep=lambda _s: None)
            report = worker.drain()
            assert executor.calls == 2
            assert report.done == 1
            assert queue.list_tasks(limit=1)[0].status == st.DONE


def test_worker_survives_executor_exception():
    with tempfile.TemporaryDirectory() as td:
        with _queue(td) as queue:
            queue.enqueue(_message("om_boom"))
            executor = _FakeExecutor([RuntimeError("执行器炸了")])
            worker = Worker(queue, executor, max_retries=0,
                            retry_backoff_seconds=0, sleep=lambda _s: None)
            report = worker.drain()
            assert report.alerted == 1             # 异常被当成失败，worker 不崩
            assert "执行器炸了" in queue.list_tasks(limit=1)[0].result


def test_worker_report_summary():
    from orchestrator.worker import WorkerReport

    assert WorkerReport().summary == "没有待处理任务"
    assert "完成 2" in WorkerReport(processed=2, done=2).summary


if __name__ == "__main__":
    test_task_state_machine()
    test_queue_enqueue_is_idempotent()
    test_queue_claim_and_steps()
    test_queue_retry_backoff_is_kept()
    test_queue_status_counts_and_listing()
    test_worker_success_path_replies()
    test_worker_retries_then_alerts()
    test_worker_recovers_after_retry()
    test_worker_survives_executor_exception()
    test_worker_report_summary()
    print("test_orchestrator.py 全部通过 ✓")
