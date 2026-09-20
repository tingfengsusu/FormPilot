"""台账 / 日报表 / 异常预警测试（设计文档 4.4、4.5）。

覆盖：
  - 台账：写入、按 task 幂等、节省时间统计、按品种分布
  - 日报表：处理量/成功率/平均耗时/失败原因 Top5 + 提效数据
  - 预警：连续失败计数、@负责人（飞书 `<at user_id=...>` 标记）
  - worker 集成：成功后写台账；告警文本里带上 @
  - 聊天命令：状态 / 报表
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.console import ConsoleChannel
from gateway.feishu import FeishuChannel
from gateway.models import InboundMessage
from ledger import Ledger
from orchestrator import state as st
from orchestrator.queue import TaskQueue
from orchestrator.worker import ExecResult, Worker
from report.alert import AlertRule, check_alert, consecutive_failures, format_alert
from report.daily import build_daily_report, format_daily_report


@contextlib.contextmanager
def _store(tmp_dir: str):
    path = os.path.join(tmp_dir, "gateway.db")
    queue = TaskQueue(path)
    ledger = Ledger(path, manual_seconds=240)
    try:
        yield queue, ledger
    finally:
        queue.close()
        ledger.close()


def _enqueue(queue: TaskQueue, msg_id: str = "m1", text: str = "学平险"):
    return queue.enqueue(InboundMessage(channel="console", msg_id=msg_id,
                                        chat_id="c1", text=text),
                         variety="学平险")


# ----------------------------------------------------------------------
# 台账
# ----------------------------------------------------------------------
def test_ledger_records_and_saves_time():
    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (queue, ledger):
            task = _enqueue(queue, "m1")
            entry = ledger.record(task.id, applicant="张伟", variety="学平险",
                                  filled_fields=5, auto_seconds=12.0)
            assert entry.manual_seconds == 240
            assert entry.saved_seconds == 228          # 240 - 12

            # 同一个 task 只写一次（重跑不重复计）
            again = ledger.record(task.id, applicant="张伟", auto_seconds=99)
            assert again.auto_seconds == 12
            assert len(ledger.list_entries()) == 1

            summary = ledger.summary()
            assert summary["count"] == 1
            assert summary["filled_fields"] == 5
            assert summary["avg_auto_seconds"] == 12.0
            assert summary["saved_seconds"] == 228
            assert summary["saved_hours"] == 0.06      # 228/3600 保留两位

            # 按品种分布
            ledger.record(task.id + 1, variety="12306", filled_fields=3,
                          auto_seconds=30)
            dist = ledger.by_variety()
            assert dist[0]["count"] == 1 and len(dist) == 2


def test_ledger_summary_empty():
    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (_queue, ledger):
            summary = ledger.summary()
            assert summary["count"] == 0 and summary["saved_hours"] == 0.0


# ----------------------------------------------------------------------
# 日报表
# ----------------------------------------------------------------------
def test_daily_report_counts_and_failures():
    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (queue, ledger):
            ok_task = _enqueue(queue, "m1")
            queue.claim_next()
            queue.set_status(ok_task.id, st.MAPPING)
            queue.set_status(ok_task.id, st.FILLING)
            queue.set_status(ok_task.id, st.VERIFYING)
            queue.set_status(ok_task.id, st.DONE, result="已填完")
            queue.add_step(ok_task.id, "FILLING", ok=True, detail="填写 5 字段",
                           elapsed_ms=3000)
            ledger.record(ok_task.id, applicant="张伟", variety="学平险",
                          filled_fields=5, auto_seconds=3.0)

            bad_task = _enqueue(queue, "m2")
            queue.claim_next()
            queue.add_step(bad_task.id, "FILLING", ok=False,
                           detail="必填属性 监护人电话 为空")
            queue.set_status(bad_task.id, st.FAILED)
            queue.set_status(bad_task.id, st.ALERT, result="必填缺失")

            report = build_daily_report(queue, ledger)
            assert report.total == 2 and report.done == 1 and report.alerted == 1
            assert report.success_rate == 50.0
            assert report.avg_seconds == 3.0
            assert report.top_failures[0]["count"] == 1
            assert "监护人电话" in report.top_failures[0]["reason"]
            assert report.saved_hours > 0

            text = format_daily_report(report)
            assert "运行日报" in text and "成功率 50.0%" in text
            assert "提效" in text and "节省" in text


def test_daily_report_no_tasks():
    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (queue, ledger):
            report = build_daily_report(queue, ledger, day="2000-01-01")
            text = format_daily_report(report)
            assert report.total == 0 and "任务 0 条" in text


# ----------------------------------------------------------------------
# 预警
# ----------------------------------------------------------------------
def test_consecutive_failures_and_alert():
    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (queue, _ledger):
            for index in range(3):
                task = _enqueue(queue, f"m{index}")
                queue.claim_next()
                queue.set_status(task.id, st.FAILED)
                queue.set_status(task.id, st.ALERT, result="页面打不开")
            assert consecutive_failures(queue) == 3

            rule = AlertRule(mention="ou_boss", consecutive_failures=3)
            alert = check_alert(queue, rule)
            assert alert is not None and "连续 3 条" in alert
            assert "ou_boss" in alert

            # 成功后清零（再补一条 DONE 在最前）
            done_task = _enqueue(queue, "m_ok")
            queue.claim_next()
            for status in (st.MAPPING, st.FILLING, st.VERIFYING, st.DONE):
                queue.set_status(done_task.id, status)
            assert consecutive_failures(queue) == 0
            assert check_alert(queue, rule) is None
            # 规则关闭时不告警
            assert check_alert(queue, AlertRule(enabled=False)) is None


def test_format_mention_per_channel():
    assert FeishuChannel.format_mention(None, "ou_abc") == \
        '<at user_id="ou_abc"></at>'
    assert FeishuChannel.format_mention(None, "all") == '<at user_id="all"></at>'
    assert FeishuChannel.format_mention(None, "") == ""
    assert ConsoleChannel(printer=lambda *_: None).format_mention("ou_abc") == "@ou_abc"
    assert format_alert("挂了", mention="@某人", task_id=3).startswith("⚠️ [任务 #3]")


# ----------------------------------------------------------------------
# worker 集成
# ----------------------------------------------------------------------
def test_worker_writes_ledger_and_mentions():
    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (queue, ledger):
            channel = ConsoleChannel(printer=lambda *_: None)
            channel.alert_mention = "ou_boss"
            _enqueue(queue, "m1")

            def executor(task, note_step):
                note_step("OCR", ok=True, detail="识别 6 字段")
                note_step("FILLING", ok=True, detail="填写 5 字段")
                return ExecResult(ok=True, summary="已填完", applicant="张伟",
                                  filled_fields=5, screenshot="")

            worker = Worker(queue, executor, channel, ledger=ledger,
                            alert_mention="ou_boss")
            worker.drain()
            entries = ledger.list_entries()
            assert len(entries) == 1
            assert entries[0].applicant == "张伟"
            assert entries[0].filled_fields == 5
            assert entries[0].auto_seconds >= 0

            # 失败 → 告警文本里带 @（飞书/console 各自的写法）
            _enqueue(queue, "m2")

            def failing(task, note_step):
                return ExecResult(ok=False, error="页面打不开")

            failing_worker = Worker(queue, failing, channel, max_retries=0,
                                    alert_mention="ou_boss",
                                    sleep=lambda _s: None)
            failing_worker.drain()
            assert any("@ou_boss" in item["text"] for item in channel.sent)


# ----------------------------------------------------------------------
# 聊天命令
# ----------------------------------------------------------------------
def test_chat_commands():
    from tools.feishu_bot import (_is_report_command, _is_status_command,
                                  _status_text)

    assert _is_status_command("状态") and _is_status_command("/status")
    assert not _is_status_command("学平险")
    assert _is_report_command("报表") and _is_report_command("日报")
    assert not _is_report_command("状态")

    with tempfile.TemporaryDirectory() as td:
        with _store(td) as (queue, _ledger):
            _enqueue(queue, "m1")
            text = _status_text(queue)
            assert "队列状态" in text and "PENDING 1" in text


if __name__ == "__main__":
    test_ledger_records_and_saves_time()
    test_ledger_summary_empty()
    test_daily_report_counts_and_failures()
    test_daily_report_no_tasks()
    test_consecutive_failures_and_alert()
    test_format_mention_per_channel()
    test_worker_writes_ledger_and_mentions()
    test_chat_commands()
    print("test_report.py 全部通过 ✓")
