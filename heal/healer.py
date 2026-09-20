"""自愈对外唯一入口（设计文档第五节）：diagnose / apply / learn / stats / seed。

调用方（worker / CLI / 测试）只需要这一个类：

    healer = Healer(store, config)
    decision = healer.diagnose_text(error, step="FILLING", property="保险期间起")
    if decision.matched and decision.auto:
        outcome = healer.apply(decision, HealContext(...))
    healer.learn(error, remedy=..., params=..., ok=False)   # 未命中时记为新案例

配置（``config/gateway.yaml`` 的 ``heal`` 段）::

    heal:
      enabled: true
      threshold: 0.45            # 相似度低于它就算未命中（不猜）
      max_heals_per_task: 1      # 同一条任务最多自愈几次（防死循环）
      auto: [adjust_period_floor, retry_after, dismiss_overlay, reload_page]
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import yaml

from .features import extract_feature, signature_of
from .models import (FailureFeature, HealCase, HealDecision, HealOutcome,
                     HealStats, SOURCE_AUTO, SOURCE_SEED)
from .remedies import HealContext, apply_remedy, get_remedy, is_auto
from .store import HealStore

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SEEDS = os.path.join(_PROJECT_ROOT, "config", "heal_cases.yaml")

#: 默认阈值：低于它就算「没命中」，宁可转人工也不乱套处置（不猜）。
DEFAULT_THRESHOLD = 0.45


class Healer:
    """故障自愈：检索案例 → 执行白名单处置 → 写回经验。"""

    def __init__(self, store: HealStore,
                 config: Optional[Dict[str, Any]] = None,
                 seeds_path: str = ""):
        config = config or {}
        self.store = store
        self.enabled = bool(config.get("enabled", True))
        self.threshold = float(config.get("threshold", DEFAULT_THRESHOLD))
        self.max_heals_per_task = int(config.get("max_heals_per_task", 1))
        auto = config.get("auto")
        self.auto_remedies = set(auto) if auto else None      # None = 用白名单自身
        self.seeds_path = seeds_path or _DEFAULT_SEEDS
        self._heals_per_task: Dict[int, int] = {}

    # ------------------------------------------------------------------
    # 种子案例
    # ------------------------------------------------------------------
    def seed(self, cases: Optional[List[Dict[str, Any]]] = None) -> int:
        """装载种子案例（默认读 ``config/heal_cases.yaml``），返回条数。"""
        if cases is None:
            cases = _load_seeds(self.seeds_path)
        count = 0
        for item in cases or []:
            case = HealCase(
                signature=str(item.get("signature") or
                              signature_of(item.get("symptom", ""))),
                symptom=str(item.get("symptom") or ""),
                step=str(item.get("step") or ""),
                variety=str(item.get("variety") or ""),
                property_name=str(item.get("property") or ""),
                cause=str(item.get("cause") or ""),
                remedy=str(item.get("remedy") or "manual"),
                remedy_params=dict(item.get("params") or {}),
                source=str(item.get("source") or SOURCE_SEED),
                enabled=bool(item.get("enabled", True)))
            self.store.upsert_case(case)
            count += 1
        return count

    # ------------------------------------------------------------------
    # 诊断
    # ------------------------------------------------------------------
    def diagnose(self, feature: FailureFeature) -> HealDecision:
        """检索案例库，给出处置决策（可自动 / 只给建议 / 未命中）。"""
        if not self.enabled:
            return HealDecision()
        if not feature.signature:
            feature.signature = signature_of(feature.symptom)
        matches = self.store.find_similar(feature, top_k=1,
                                          threshold=self.threshold)
        if not matches:
            return HealDecision()
        case, score = matches[0]
        auto = self._can_auto(case)
        hint = self._hint_of(case)
        self.store.record_seen(case.id)
        return HealDecision(case=case, similarity=score, auto=auto, hint=hint)

    def diagnose_text(self, error: str, step: str = "", property_name: str = "",
                      variety: str = "") -> HealDecision:
        """便捷入口：直接给错误文本（内部做特征提取）。"""
        return self.diagnose(extract_feature(error, step=step, property_name=property_name,
                                             variety=variety))

    def _can_auto(self, case: HealCase) -> bool:
        """能不能自动执行：白名单自身允许 + 配置没把它关掉。"""
        if not is_auto(case.remedy):
            return False
        if self.auto_remedies is not None and case.remedy not in self.auto_remedies:
            return False
        return True

    @staticmethod
    def _hint_of(case: HealCase) -> str:
        hint = str((case.remedy_params or {}).get("hint") or "")
        if hint:
            return hint
        spec = get_remedy(case.remedy)
        return spec.description if spec else ""

    # ------------------------------------------------------------------
    # 执行处置
    # ------------------------------------------------------------------
    def apply(self, decision: HealDecision, context: HealContext,
              task_id: Optional[int] = None) -> HealOutcome:
        """执行处置（三重检查都在 :func:`heal.remedies.apply_remedy` 里）。"""
        if not self.enabled:
            return HealOutcome(applied=False, detail="自愈已关闭")
        outcome = apply_remedy(decision, context)
        if decision.matched and task_id is not None:
            self.store.record_attempt(
                task_id=task_id, case_id=decision.case.id if outcome.applied else None,
                similarity=decision.similarity,
                remedy=decision.case.remedy, applied=outcome.applied,
                ok=outcome.ok, detail=outcome.detail)
        return outcome

    def check_budget(self, task_id: int) -> bool:
        """同一条任务还能不能再自愈一次（防死循环）。"""
        return self._heals_per_task.get(int(task_id), 0) < self.max_heals_per_task

    def note_heal(self, task_id: int) -> None:
        self._heals_per_task[int(task_id)] = \
            self._heals_per_task.get(int(task_id), 0) + 1

    # ------------------------------------------------------------------
    # 学习
    # ------------------------------------------------------------------
    def learn(self, error: str, remedy: str = "manual",
              params: Optional[Dict[str, Any]] = None, ok: bool = False,
              step: str = "", property_name: str = "", variety: str = "",
              cause: str = "") -> int:
        """把一次失败记成案例（未命中时的沉淀），返回 case_id。

        :param ok: 这条新案例的处置是否验证过有效（默认未验证 → manual）
        """
        feature = extract_feature(error, step=step, property_name=property_name,
                                  variety=variety)
        if not feature.signature:
            return 0
        case = HealCase(
            signature=feature.signature, symptom=feature.symptom,
            step=step, variety=variety, property_name=property_name, cause=cause,
            remedy=remedy, remedy_params=dict(params or {}),
            source=SOURCE_AUTO, enabled=bool(ok))
        saved = self.store.upsert_case(case)
        return int(saved.id or 0)

    def mark_task_success(self, task_id: int) -> List[int]:
        """任务最终成功：结算这条任务上的自愈尝试，记入案例命中率。"""
        case_ids = self.store.settle_attempts(task_id, ok=True)
        for case_id in case_ids:
            self.store.record_hit(case_id, success=True)
        return case_ids

    def mark_task_failed(self, task_id: int) -> List[int]:
        """任务最终失败：结算尝试（命中但没救回来）。"""
        case_ids = self.store.settle_attempts(task_id, ok=False)
        for case_id in case_ids:
            self.store.record_hit(case_id, success=False)
        return case_ids

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def stats(self, day: str = "") -> HealStats:
        return self.store.stats(day)

    def cases(self, enabled_only: bool = False) -> List[HealCase]:
        return self.store.list_cases(enabled_only=enabled_only)


def _load_seeds(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 — 种子文件坏了不该让主流程起不来
        return []
    cases = data.get("cases") if isinstance(data, dict) else data
    return [dict(item) for item in (cases or []) if isinstance(item, dict)]


__all__ = ["Healer", "DEFAULT_THRESHOLD"]
