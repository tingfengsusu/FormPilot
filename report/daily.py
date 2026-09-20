"""日终报表（设计文档 4.5）：处理量 / 成功率 / 平均耗时 / 失败原因 Top5。

数据来自两处：

- ``task`` / ``task_step``：处理量、成功率、失败原因、平均耗时
  （失败原因取自 **ok=0 的步骤**——重试后成功的任务也能看出它失败过）
- ``ledger``：**提效数据**（人工预估耗时 vs 实际自动耗时）——设计文档说
  这才是业绩来源：「平均每单省 X 分钟 / 今日累计省 Y 小时」
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from orchestrator import state as st


@dataclass
class DailyReport:
    """某一天的运行报表。"""

    day: str = ""
    total: int = 0                 # 当天任务数
    done: int = 0                  # 完成
    alerted: int = 0               # 重试耗尽，需人工介入
    running: int = 0               # 排队中 / 执行中
    retried: int = 0               # 累计重试次数
    healed: int = 0                # 自愈成功次数（HEAL 步骤）
    heal_failed: int = 0           # 自愈尝试但没救回来
    success_rate: float = 0.0      # 完成 /（完成 + 待人工）
    avg_seconds: float = 0.0       # 平均「填表」耗时（task_step 的 FILLING）
    top_failures: List[Dict[str, Any]] = field(default_factory=list)
    ledger: Dict[str, Any] = field(default_factory=dict)

    @property
    def saved_hours(self) -> float:
        return float(self.ledger.get("saved_hours") or 0.0)


def build_daily_report(queue: Any, ledger: Any = None,
                       day: str = "") -> DailyReport:
    """汇总某天的任务与台账（``day`` 留空 = 今天）。"""
    day = day or datetime.now().strftime("%Y-%m-%d")
    tasks = queue.list_tasks(day=day, limit=1000)
    report = DailyReport(day=day, total=len(tasks))

    elapsed_total = 0.0
    elapsed_count = 0
    failures: Counter = Counter()

    for task in tasks:
        if task.status == st.DONE:
            report.done += 1
        elif task.status == st.ALERT:
            report.alerted += 1
        else:
            report.running += 1
        report.retried += int(task.retry_count or 0)
        heal_applied = False
        for step in queue.steps(task.id):
            if step["step"] == "FILLING" and step["elapsed_ms"]:
                elapsed_total += float(step["elapsed_ms"]) / 1000.0
                elapsed_count += 1
            if step["step"] == "HEAL" and step["ok"]:
                heal_applied = True          # 这条任务上执行过自愈
            if not step["ok"] and step["step"] != "HEAL":
                detail = str(step["detail"] or "").strip()[:60]
                if detail:
                    failures[f"[{step['step']}] {detail}"] += 1
        # 「自愈 N 次」的判定看**任务最终有没有救回来**（动作执行了不等于成功）
        if heal_applied:
            if task.status == st.DONE:
                report.healed += 1
            elif task.status == st.ALERT:
                report.heal_failed += 1

    finished = report.done + report.alerted
    report.success_rate = round(report.done / finished * 100, 1) if finished else 0.0
    report.avg_seconds = round(elapsed_total / elapsed_count, 1) \
        if elapsed_count else 0.0
    report.top_failures = [{"reason": reason, "count": count}
                           for reason, count in failures.most_common(5)]
    if ledger is not None:
        report.ledger = ledger.summary(day)
    return report


def format_daily_report(report: DailyReport) -> str:
    """报表文本（直接回推到消息端）。"""
    lines = [
        f"📊 {report.day} 运行日报",
        f"任务 {report.total} 条：完成 {report.done}，待人工 {report.alerted}，"
        f"排队/执行中 {report.running}"
        + (f"，重试 {report.retried} 次" if report.retried else ""),
        f"成功率 {report.success_rate}%　平均填表耗时 {report.avg_seconds}s",
    ]
    if report.healed or report.heal_failed:
        lines.append(f"🩹 自愈 {report.healed} 次"
                     + (f"（另有 {report.heal_failed} 次没救回来）"
                        if report.heal_failed else "")
                     + "——故障经验已回写案例库")
    info = report.ledger or {}
    if info.get("count"):
        lines.append(
            f"提效：{info['count']} 单共节省 {info.get('saved_hours', 0)} 小时"
            f"（人工预估 {info.get('manual_seconds', 0)}s/单 → "
            f"实际 {info.get('avg_auto_seconds', 0)}s/单）")
    if report.top_failures:
        lines.append("失败原因 Top：")
        lines += [f"  · {item['reason']}（{item['count']} 次）"
                  for item in report.top_failures]
    return "\n".join(lines)


__all__ = ["DailyReport", "build_daily_report", "format_daily_report"]
