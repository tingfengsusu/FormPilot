"""异常自愈演示：失败 → 命中案例 → 自动处置 → 重试成功（不需要浏览器/凭据）。

    python tools/demo_heal.py

跑在一个临时库上，不碰 data/gateway.db（你正在跑的机器人不受影响）。
演示内容：

    1. 装配：案例库（种子 6 条）+ worker（自愈开启）
    2. 第 1 次执行：抛出真机踩过的错误「日期选择器…日期不可选」
    3. 自愈：命中案例 #1（adjust_period_floor）→ 自动放宽起期下限 → 重新入队
    4. 第 2 次执行：成功 → 任务 DONE，案例命中率更新，日报记「自愈 1 次」
    5. 再演示一个「库里没有的故障」：不猜 → 记为新案例（禁用）+ 告警
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.console import ConsoleChannel
from gateway.models import InboundMessage
from heal import Healer, HealStore
from heal.remedies import HealContext
from orchestrator import state as st
from orchestrator.queue import TaskQueue
from orchestrator.worker import ExecResult, Worker
from report.daily import build_daily_report, format_daily_report

#: 真机踩过的两个错误（与 config/heal_cases.yaml 的种子同源）
DATE_ERROR = "日期选择器结构不可识别或日期不可选（目标 2026-09-21）"
UNKNOWN_ERROR = "识别失败：云 OCR 返回 401 未授权（app_id 无效）"


class _DemoExecutor:
    """第 1 次失败（日期不可选），自愈之后再跑就成功。"""

    def __init__(self, error: str, error_step: str = "FILLING",
                 error_property: str = "保险期间起"):
        self.error = error
        self.error_step = error_step
        self.error_property = error_property
        self.calls = 0

    def __call__(self, task, note_step):
        self.calls += 1
        note_step("OCR", ok=True, detail="识别 6 个字段")
        note_step("MAPPING", ok=True, detail="映射 5 个属性")
        if self.calls == 1:
            note_step(self.error_step, ok=False, detail=self.error)
            return ExecResult(ok=False, error=self.error,
                              error_step=self.error_step,
                              error_property=self.error_property)
        note_step("FILLING", ok=True, detail="填写 5 个字段")
        note_step("VERIFYING", ok=True, detail="回读 0 差异")
        return ExecResult(ok=True, summary="已填完 5 个字段", applicant="演示学生",
                          filled_fields=5)


def _enqueue(queue: TaskQueue, msg_id: str, text: str = "学平险") -> None:
    queue.enqueue(InboundMessage(channel="console", msg_id=msg_id,
                                 chat_id="demo", text=text), variety="学平险")


def _print_steps(queue: TaskQueue, task_id: int, title: str) -> None:
    print(f"\n{title}")
    for step in queue.steps(task_id):
        mark = "✓" if step["ok"] else "✗"
        print(f"  {mark} {step['step']:<10} {step['detail'][:70]}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="formpilot_heal_demo_")
    db_path = os.path.join(tmp, "gateway.db")
    queue, store = TaskQueue(db_path), HealStore(db_path)
    healer = Healer(store)
    print(f"案例库：装载种子 {healer.seed()} 条（库文件：{db_path}）")
    channel = ConsoleChannel(printer=lambda text: print(f"  {text}"))

    # —— ① 已知故障：命中案例 → 自动处置 → 重试成功 ——
    print("\n" + "=" * 62)
    print("① 已知故障：日期不可选（真机踩过）")
    print("=" * 62)
    floors: list = []
    worker = Worker(queue, _DemoExecutor(DATE_ERROR), channel, healer=healer,
                    heal_context=HealContext(
                        adjust_period_floor=lambda days: floors.append(days) or True),
                    max_retries=1, retry_backoff_seconds=0, sleep=lambda _s: None)
    _enqueue(queue, "demo-1")
    report = worker.drain()
    task = queue.list_tasks(limit=1)[0]
    _print_steps(queue, task.id, f"任务 #{task.id} 的步骤（最终状态：{task.status}）")
    print(f"  执行器被调用 {2} 次；起期下限被放宽了 {floors} 天")

    case = [c for c in healer.cases() if c.remedy == "adjust_period_floor"][0]
    print(f"\n案例 #1 统计：{case.describe()}")
    attempts = healer.store.attempts(task.id)
    print(f"自愈留痕：命中案例 #{attempts[0]['case_id']}，"
          f"相似度 {attempts[0]['similarity']:.2f}，"
          f"处置 {attempts[0]['remedy']}，结果 {'成功' if attempts[0]['ok'] else '失败'}")

    # —— ② 未知故障：不猜 → 记新案例 + 告警 ——
    print("\n" + "=" * 62)
    print("② 未知故障：云 OCR 401（案例库里没有）")
    print("=" * 62)
    before = len(healer.cases())
    worker2 = Worker(queue, _DemoExecutor(UNKNOWN_ERROR, error_step="OCR",
                                          error_property="id_card"),
                     channel, healer=healer,
                     max_retries=0, retry_backoff_seconds=0, sleep=lambda _s: None)
    _enqueue(queue, "demo-2")
    worker2.drain()
    task2 = queue.list_tasks(limit=1)[0]
    _print_steps(queue, task2.id, f"任务 #{task2.id} 的步骤（最终状态：{task2.status}）")
    new_cases = [c for c in healer.cases() if c.source == "auto"]
    print(f"  新增案例 {len(healer.cases()) - before} 条"
          + (f"：#{new_cases[0].id}（enabled={new_cases[0].enabled}，"
             f"等人工确认处置后才生效）" if new_cases else ""))

    # —— ③ 汇总 ——
    print("\n" + "=" * 62)
    daily = build_daily_report(queue, ledger=None)
    print(format_daily_report(daily))
    print("\n" + healer.stats().describe())
    store.close()
    queue.close()
    print(f"\n（演示库在临时目录，可删：{tmp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
