"""处置动作白名单与应用器 —— **自愈唯一的安全边界**（设计文档第六节）。

原则：

- 只有白名单里的动作能被自动执行，参数受 schema 约束
- 需要**改配置/改代码**的动作（换 locator、补别名表）一律只给建议，由人确认
- 动作能不能执行还取决于**上下文能力**：例如 ``retry_after`` 需要 sleep、
  ``dismiss_overlay`` 需要浏览器页面。上下文给不出来 → 降级为人工建议，不硬来

所以「自动执行」= 白名单 ∩ 上下文支持 ∩ 参数合法，三者缺一不可。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from .models import (REMEDY_ADJUST_PERIOD_FLOOR, REMEDY_DISMISS_OVERLAY,
                     REMEDY_MANUAL, REMEDY_RELOAD_PAGE, REMEDY_RETRY_AFTER,
                     REMEDY_SWITCH_LOCATOR, HealDecision, HealOutcome)


@dataclass(frozen=True)
class RemedySpec:
    """一个处置动作的说明（白名单条目）。"""

    name: str
    auto: bool                    # 是否允许自动执行
    description: str
    requires: tuple = ()          # 需要的上下文能力
    params: tuple = ()            # 允许的参数名


REMEDIES: Dict[str, RemedySpec] = {
    REMEDY_ADJUST_PERIOD_FLOOR: RemedySpec(
        REMEDY_ADJUST_PERIOD_FLOOR, True,
        "放宽起期下限 N 天后重试（页面只允许从某天起投保）",
        requires=("adjust_period_floor",), params=("days",)),
    REMEDY_RETRY_AFTER: RemedySpec(
        REMEDY_RETRY_AFTER, True, "等 N 秒后重试（站点限流/网络抖动）",
        requires=("sleep",), params=("seconds",)),
    REMEDY_DISMISS_OVERLAY: RemedySpec(
        REMEDY_DISMISS_OVERLAY, True, "关掉遮挡的弹窗/遮罩后重试",
        requires=("dismiss_overlay",), params=()),
    REMEDY_RELOAD_PAGE: RemedySpec(
        REMEDY_RELOAD_PAGE, True, "重新加载页面（渲染失败/登录态）",
        requires=("reload_page",), params=()),
    REMEDY_SWITCH_LOCATOR: RemedySpec(
        REMEDY_SWITCH_LOCATOR, False,
        "换备用 locator（页面改版）——需人工确认配置，不自动改",
        params=("property", "locator")),
    REMEDY_MANUAL: RemedySpec(
        REMEDY_MANUAL, False, "转人工（补别名表、换二维码链接等）",
        params=("hint",)),
}


def get_remedy(name: str) -> Optional[RemedySpec]:
    return REMEDIES.get(str(name or "").strip())


def is_auto(name: str) -> bool:
    spec = get_remedy(name)
    return bool(spec and spec.auto)


@dataclass
class HealContext:
    """调用方能提供的能力（缺哪个就不能执行依赖它的动作）。"""

    sleep: Optional[Callable[[float], None]] = None
    adjust_period_floor: Optional[Callable[[int], bool]] = None
    dismiss_overlay: Optional[Callable[[], bool]] = None
    reload_page: Optional[Callable[[], bool]] = None
    task_id: Optional[int] = None

    def has(self, capability: str) -> bool:
        return callable(getattr(self, capability, None))


def validate_params(name: str, params: Dict[str, Any]) -> str:
    """参数合法性检查，返回错误说明（合法则空串）。"""
    spec = get_remedy(name)
    if spec is None:
        return f"未知的处置动作：{name}"
    extra = set(params or {}) - set(spec.params)
    if extra:
        return f"{name} 不支持参数：{'、'.join(sorted(extra))}"
    if name == REMEDY_ADJUST_PERIOD_FLOOR:
        days = params.get("days", 1)
        if not isinstance(days, int) or not (1 <= days <= 30):
            return f"adjust_period_floor.days 应为 1~30 的整数，实际 {days!r}"
    if name == REMEDY_RETRY_AFTER:
        seconds = params.get("seconds", 30)
        if not isinstance(seconds, (int, float)) or not (1 <= seconds <= 3600):
            return f"retry_after.seconds 应为 1~3600 秒，实际 {seconds!r}"
    return ""


def apply_remedy(decision: HealDecision, context: HealContext) -> HealOutcome:
    """执行命中案例里的处置动作（白名单 + 上下文 + 参数三重检查）。"""
    if decision is None or not decision.matched:
        return HealOutcome(applied=False, detail="没有命中案例")
    case = decision.case
    name = case.remedy
    params = dict(case.remedy_params or {})

    spec = get_remedy(name)
    if spec is None:
        return HealOutcome(applied=False, detail=f"未知处置动作：{name}")
    if not spec.auto:
        return HealOutcome(
            applied=False,
            detail=f"{name} 不在自动白名单（需人工确认）"
                   + (f"；建议：{params.get('hint')}" if params.get("hint") else ""))
    invalid = validate_params(name, params)
    if invalid:
        return HealOutcome(applied=False, detail=f"参数非法：{invalid}")
    missing = [cap for cap in spec.requires if not context.has(cap)]
    if missing:
        return HealOutcome(
            applied=False,
            detail=f"当前上下文不支持该动作（缺 {'、'.join(missing)}）——已降级为人工建议")

    try:
        if name == REMEDY_ADJUST_PERIOD_FLOOR:
            days = int(params.get("days", 1))
            ok = bool(context.adjust_period_floor(days))
            return HealOutcome(applied=True, ok=ok,
                               detail=f"起期下限放宽 {days} 天")
        if name == REMEDY_RETRY_AFTER:
            seconds = float(params.get("seconds", 30))
            context.sleep(seconds)
            return HealOutcome(applied=True, ok=True, detail=f"已等待 {seconds:.0f}s")
        if name == REMEDY_DISMISS_OVERLAY:
            ok = bool(context.dismiss_overlay())
            return HealOutcome(applied=True, ok=ok, detail="已尝试关闭遮挡层")
        if name == REMEDY_RELOAD_PAGE:
            ok = bool(context.reload_page())
            return HealOutcome(applied=True, ok=ok, detail="已重新加载页面")
    except Exception as e:  # noqa: BLE001 — 处置本身失败也算「没成功」，不能炸主流程
        return HealOutcome(applied=True, ok=False, detail=f"执行异常：{e}")
    return HealOutcome(applied=False, detail=f"{name} 没有对应实现")


def describe_remedy(name: str) -> str:
    spec = get_remedy(name)
    return spec.description if spec else f"（未知动作 {name}）"


__all__ = ["REMEDIES", "RemedySpec", "HealContext", "get_remedy", "is_auto",
           "validate_params", "apply_remedy", "describe_remedy"]
