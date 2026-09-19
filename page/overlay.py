"""页面遮挡物处理 —— 点任何东西之前先确认没有弹窗挡着。

**实测背景（太保投保页）**：打开页面会弹「温馨提示 / 投保须知」底部弹窗
（Vant ``van-popup`` + 全屏 ``van-overlay``，按钮是倒计时的「我知道了(3s)」）。
更麻烦的是：**它会在点击表单后再次弹出** —— 于是所有后续点击都被遮罩
拦截，报错却是「控件类型未识别」「点击超时」，非常误导（排查了两轮才定位到）。

所以本模块提供两件事：
  - :func:`dismiss_blocking_popups`：关掉挡住页面的弹窗（能等倒计时）
  - :func:`click_with_guard`：点击前先关弹窗；被拦截时关掉再重试一次
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

#: 弹窗里的「确认」按钮文案（实测：「我知道了(3s)」倒计时按钮）。
CONFIRM_WORDS = ("我知道了", "我已知晓", "已知晓", "知道了", "确定", "同意",
                 "继续", "好的", "关闭")

#: 找挡住页面的弹窗并点掉它的确认按钮。
_POPUP_JS = """() => {
    const WORDS = %s;
    const visible = el => {
        const s = getComputedStyle(el), r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && s.display !== 'none'
            && s.visibility !== 'hidden' && +s.opacity > 0;
    };
    const vw = innerWidth, vh = innerHeight;
    const overlays = [...document.querySelectorAll('body *')].filter(el => {
        const s = getComputedStyle(el);
        if (s.position !== 'fixed' && s.position !== 'absolute') return false;
        if (!visible(el)) return false;
        const r = el.getBoundingClientRect();
        return r.width * r.height >= vw * vh * 0.2;   // 覆盖 ≥20%% 视口
    });
    const texts = overlays
        .map(el => (el.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 90))
        .filter(Boolean);
    let clicked = null, pending = null;
    for (const box of overlays) {
        for (const node of box.querySelectorAll('button, a, [role="button"], div, span')) {
            const t = (node.textContent || '').replace(/\\s+/g, '');
            if (!t || t.length > 14) continue;
            if (!WORDS.some(w => t.includes(w))) continue;
            if (!visible(node)) continue;
            if (node.tagName !== 'BUTTON' && node.tagName !== 'A' && node.children.length) continue;
            const s = getComputedStyle(node);
            const disabled = node.disabled || node.getAttribute('aria-disabled') === 'true'
                || /disabled/i.test(String(node.className)) || s.pointerEvents === 'none';
            if (disabled) { pending = t; continue; }   // 倒计时按钮：先等它可用
            node.click();
            clicked = t;
            break;
        }
        if (clicked) break;
    }
    return {clicked: clicked, pending: pending, overlays: texts.slice(0, 5)};
}""" % (list(CONFIRM_WORDS),)


def dismiss_blocking_popups(page: Any, rounds: int = 8,
                            interval_seconds: float = 1.0) -> Dict[str, Any]:
    """关掉挡住页面的弹窗（返回处理结果，便于报告给使用者）。

    :return: ``{"dismissed": [点掉的按钮文案], "pending": 倒计时中的按钮文案,
                "overlays": [仍存在的遮挡文本]}``
    """
    dismissed: List[str] = []
    pending = ""
    overlays: List[str] = []
    for _ in range(max(1, rounds)):
        try:
            info = page.evaluate(_POPUP_JS) or {}
        except Exception:  # noqa: BLE001 — 读不到就当没有弹窗
            break
        overlays = [str(t) for t in (info.get("overlays") or [])]
        pending = str(info.get("pending") or "")
        clicked = info.get("clicked")
        if clicked:
            dismissed.append(str(clicked))
            page.wait_for_timeout(int(interval_seconds * 1000))
            continue
        if overlays:
            # 有遮罩但点不到按钮：可能是倒计时（「我知道了(3s)」）→ 等一轮再看
            page.wait_for_timeout(int(interval_seconds * 1000))
            continue
        break
    return {"dismissed": dismissed, "pending": pending, "overlays": overlays}


def format_popup_report(info: Dict[str, Any]) -> str:
    """弹窗处理结果的文字说明（空字符串表示没有需要说明的）。"""
    dismissed = info.get("dismissed") or []
    overlays = info.get("overlays") or []
    pending = info.get("pending") or ""
    if dismissed:
        line = f"已自动关闭引导弹窗：{'、'.join(dismissed)}"
        if overlays:
            line += f"；但仍检测到遮挡：{' / '.join(overlays[:2])}"
        return line
    if overlays:
        extra = f"（按钮「{pending}」可能还在倒计时）" if pending else ""
        return (f"⚠ 检测到遮挡表单的弹窗{extra}：{' / '.join(overlays[:2])}"
                f"——请手动关闭后重跑，或加 --keep-open 在浏览器里处理")
    return ""


def click_with_guard(page: Any, locator: str, timeout: int = 5000,
                     dismiss_rounds: int = 6,
                     interval_seconds: float = 1.0,
                     settle_ms: int = 250) -> str:
    """点击前先关弹窗；被遮挡拦截时再关一次并重试。

    :return: 实际点击用的 locator
    :raises Exception: 两次都被拦截（原样抛出最后一次的错误）
    """
    dismiss_blocking_popups(page, rounds=dismiss_rounds,
                            interval_seconds=interval_seconds)
    try:
        page.locator(locator).first.click(timeout=timeout)
        page.wait_for_timeout(settle_ms)
        return locator
    except Exception as first_error:  # noqa: BLE001
        # 被新弹出来的遮罩拦住 → 关掉再试一次（实测该页会「点了又弹」）
        info = dismiss_blocking_popups(page, rounds=dismiss_rounds,
                                       interval_seconds=interval_seconds)
        if not info.get("dismissed"):
            raise
        try:
            page.wait_for_timeout(settle_ms)
            page.locator(locator).first.click(timeout=timeout)
            page.wait_for_timeout(settle_ms)
            return locator
        except Exception:  # noqa: BLE001
            raise first_error


def click_js(page: Any, locator: str) -> bool:
    """用 JS 直接派发点击（绕过可点击性检查，作为最后兜底）。"""
    try:
        return bool(page.locator(locator).first.evaluate(
            """el => {
                const r = el.getBoundingClientRect();
                const opts = {bubbles: true, cancelable: true,
                              clientX: r.x + r.width / 2, clientY: r.y + r.height / 2};
                el.dispatchEvent(new MouseEvent('mousedown', opts));
                el.dispatchEvent(new MouseEvent('mouseup', opts));
                el.click();
                return true;
            }"""))
    except Exception:  # noqa: BLE001
        return False


def has_blocking_overlay(page: Any) -> bool:
    """当前是否有大块遮挡（不点击，只探测）。"""
    try:
        info = page.evaluate(_POPUP_JS) or {}
    except Exception:  # noqa: BLE001
        return False
    return bool(info.get("overlays"))


__all__ = ["dismiss_blocking_popups", "format_popup_report", "click_with_guard",
           "click_js", "has_blocking_overlay", "CONFIRM_WORDS"]
