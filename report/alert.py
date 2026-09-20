"""异常预警（设计文档 4.5）：重试耗尽 / 连续失败 → 消息端 @负责人。

两件事：

1. :func:`format_alert` —— 生成带 @负责人 的告警文本
   （飞书的 @ 是 `<at user_id="ou_xxx"></at>` 这种标记，由
   :meth:`gateway.base.Channel.format_mention` 负责平台差异）
2. :func:`consecutive_failures` / :func:`check_alert` —— 连续失败 N 次时
   额外告警一次（单条失败由 worker 已经报了，这里管「批量性的坏味道」）

告警只发**一次**（同一批连续失败不刷屏），由调用方记录冷却时间。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from orchestrator import state as st


@dataclass
class AlertRule:
    """预警规则。"""

    enabled: bool = True
    mention: str = ""                     # 飞书 open_id；"all" = @所有人
    consecutive_failures: int = 3         # 连续失败达到这个数 → 额外告警


def consecutive_failures(queue: Any, limit: int = 50) -> int:
    """从最近往前的**连续**失败（ALERT）条数。碰到成功就清零。"""
    count = 0
    for task in queue.list_tasks(limit=limit):
        if task.status == st.ALERT:
            count += 1
            continue
        if task.status == st.DONE:
            break
        # 排队/执行中的不算，继续往前看
    return count


def check_alert(queue: Any, rule: AlertRule) -> Optional[str]:
    """按规则判断是否需要额外告警，需要则返回告警文本。"""
    if not rule.enabled:
        return None
    count = consecutive_failures(queue)
    if count < max(1, int(rule.consecutive_failures)):
        return None
    return format_alert(
        f"连续 {count} 条任务失败（重试已耗尽）——建议检查投保页是否改版、"
        f"登录态是否失效，或站点是否限流", mention=rule.mention)


def format_alert(reason: str, mention: str = "", task_id: Optional[int] = None) -> str:
    """告警文本（``mention`` 由 Channel.format_mention 渲染成平台标记）。"""
    prefix = f"[任务 #{task_id}] " if task_id else ""
    head = f"⚠️ {prefix}异常预警"
    body = f"\n{reason}"
    return f"{head}{body}" + (f"\n{mention}" if mention else "")


__all__ = ["AlertRule", "check_alert", "consecutive_failures", "format_alert"]
