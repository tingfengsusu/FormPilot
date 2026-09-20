"""可观测与预警（设计文档 4.5）：

- :mod:`report.daily` 日终报表：处理量 / 成功率 / 平均耗时 / 失败原因 Top5
- :mod:`report.alert` 异常预警：重试耗尽、连续失败 → 消息端 @负责人

报表的「提效数据」来自 :mod:`ledger.db`——那才是能写进业绩的数字。
"""
from .alert import AlertRule, format_alert
from .daily import DailyReport, build_daily_report, format_daily_report

__all__ = ["DailyReport", "build_daily_report", "format_daily_report",
           "AlertRule", "format_alert"]
