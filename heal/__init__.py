"""异常自愈：把「一次人工排查出来的处置」沉淀成「下次自动命中并执行」。

设计规格见《改造设计-异常自愈知识库.md》，关键约束：

- **按相似度命中**（不是精确匹配）——页面小改版后错误文本变了仍能命中老案例
- **处置动作受控白名单**（:mod:`heal.remedies`）——需要改配置/改代码的一律只给建议
- **不猜**：未命中的故障记为新案例并转人工，不做任何自动动作
- **防死循环**：同一条任务最多自愈 N 次（配置，默认 1）

模块：
  models     FailureFeature / HealCase / HealDecision / HealOutcome / HealStats
  features   故障特征提取与归一化（signature）
  store      SQLite 案例库 + 相似检索（MVP；P2 换向量实现，接口不变）
  remedies   处置动作白名单 + 应用器（唯一执行点）
  healer     对外唯一入口：diagnose / apply / learn / stats / seed
"""
from .healer import Healer
from .models import (FailureFeature, HealCase, HealDecision, HealOutcome,
                     HealStats, REMEDY_ADJUST_PERIOD_FLOOR,
                     REMEDY_DISMISS_OVERLAY, REMEDY_MANUAL, REMEDY_RELOAD_PAGE,
                     REMEDY_RETRY_AFTER, REMEDY_SWITCH_LOCATOR)
from .store import HealStore

__all__ = ["Healer", "HealStore", "FailureFeature", "HealCase", "HealDecision",
           "HealOutcome", "HealStats", "REMEDY_ADJUST_PERIOD_FLOOR",
           "REMEDY_RETRY_AFTER", "REMEDY_DISMISS_OVERLAY", "REMEDY_RELOAD_PAGE",
           "REMEDY_SWITCH_LOCATOR", "REMEDY_MANUAL"]
