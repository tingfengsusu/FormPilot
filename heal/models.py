"""自愈模块的数据模型（见设计文档第四节）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------- 处置动作（白名单枚举，见设计文档第六节）----------
REMEDY_ADJUST_PERIOD_FLOOR = "adjust_period_floor"   # 放宽起期下限 N 天
REMEDY_RETRY_AFTER = "retry_after"                   # 等 N 秒重试（限流/网络）
REMEDY_DISMISS_OVERLAY = "dismiss_overlay"           # 关弹窗/遮罩后重试
REMEDY_RELOAD_PAGE = "reload_page"                   # 重新加载页面
REMEDY_SWITCH_LOCATOR = "switch_locator"             # 换备用 locator（只给建议）
REMEDY_MANUAL = "manual"                             # 转人工（只给建议）

#: 案例来源。
SOURCE_SEED = "seed"       # 随仓库自带的种子案例
SOURCE_AUTO = "auto"       # 运行时未命中后自动记录
SOURCE_MANUAL = "manual"   # 人工录入


@dataclass
class FailureFeature:
    """一次失败的**结构化**特征（治理自愈的输入）。

    :param symptom: 原始错误文本
    :param step: OCR / MAPPING / FILLING / VERIFYING
    :param property_name: 出问题的属性名（如「保险期间起」）
    :param variety: 品种（可作检索加分）
    :param signature: 归一化后的症状指纹（由 :mod:`heal.features` 填）

    字段名用 ``property_name`` 而不是 ``property``——后者会把内置的
    ``property`` 装饰器盖掉（dataclass 里的 ``@property`` 会直接报
    「'str' object is not callable」，实测踩过）。
    """

    symptom: str = ""
    step: str = ""
    property_name: str = ""
    variety: str = ""
    signature: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"symptom": self.symptom, "step": self.step,
                "property": self.property_name, "variety": self.variety,
                "signature": self.signature}


@dataclass
class HealCase:
    """一条故障案例：症状 → 处置（可含参数）。"""

    id: Optional[int] = None
    signature: str = ""
    symptom: str = ""
    step: str = ""
    variety: str = ""
    property_name: str = ""
    cause: str = ""
    remedy: str = REMEDY_MANUAL
    remedy_params: Dict[str, Any] = field(default_factory=dict)
    source: str = SOURCE_SEED
    enabled: bool = True
    occurrences: int = 0
    hits: int = 0
    successes: int = 0
    last_seen_at: str = ""
    created_at: str = ""

    @property
    def hit_rate(self) -> float:
        return round(self.successes / self.hits * 100, 1) if self.hits else 0.0

    def describe(self) -> str:
        params = json.dumps(self.remedy_params, ensure_ascii=False) \
            if self.remedy_params else ""
        return (f"#{self.id} [{self.remedy}{(' ' + params) if params else ''}] "
                f"{self.signature or self.symptom[:40]}"
                f"（命中 {self.hits} 次，成功 {self.successes} 次，"
                f"命中率 {self.hit_rate}%）")


@dataclass
class HealDecision:
    """一次诊断结论。"""

    case: Optional[HealCase] = None
    similarity: float = 0.0
    auto: bool = False            # 处置可以自动执行（白名单 + 上下文支持）
    hint: str = ""                # 给人工的建议（manual 或不可自动时）

    @property
    def matched(self) -> bool:
        return self.case is not None

    def describe(self) -> str:
        if not self.matched:
            return "未命中任何案例（记为新案例并转人工）"
        head = f"命中案例 {self.case.describe()}（相似度 {self.similarity:.2f}）"
        if self.auto:
            return head + " → 自动处置"
        return head + f" → 只给建议：{self.hint or self.case.remedy}"


@dataclass
class HealOutcome:
    """一次处置执行的结果。"""

    applied: bool = False         # 是否真的执行了动作
    ok: bool = False              # 执行后是否按预期生效
    detail: str = ""

    def describe(self) -> str:
        if not self.applied:
            return f"未执行：{self.detail}"
        return f"{'已执行' if self.ok else '执行但未成功'}：{self.detail}"


@dataclass
class HealStats:
    """自愈统计（日报与聊天命令用）。"""

    day: str = ""
    attempts: int = 0             # 自愈尝试次数
    applied: int = 0              # 真的执行了动作的次数
    succeeded: int = 0            # 执行后成功的次数
    unmatched: int = 0            # 未命中（记为新案例）的次数
    cases: int = 0                # 案例总数
    top_cases: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def hit_rate(self) -> float:
        return round(self.succeeded / self.applied * 100, 1) if self.applied else 0.0

    def describe(self) -> str:
        lines = [f"🩹 自愈统计（{self.day or '全部'}）",
                 f"案例库 {self.cases} 条　尝试 {self.attempts} 次"
                 f"（执行 {self.applied}，成功 {self.succeeded}，"
                 f"未命中 {self.unmatched}）"]
        if self.applied:
            lines.append(f"自愈成功率 {self.hit_rate}%")
        if self.top_cases:
            lines.append("命中最多的案例：")
            lines += [f"  · {item['describe']}（{item['hits']} 次）"
                      for item in self.top_cases[:3]]
        return "\n".join(lines)


__all__ = ["FailureFeature", "HealCase", "HealDecision", "HealOutcome",
           "HealStats", "REMEDY_ADJUST_PERIOD_FLOOR", "REMEDY_RETRY_AFTER",
           "REMEDY_DISMISS_OVERLAY", "REMEDY_RELOAD_PAGE",
           "REMEDY_SWITCH_LOCATOR", "REMEDY_MANUAL", "SOURCE_SEED",
           "SOURCE_AUTO", "SOURCE_MANUAL"]
