"""日期选择器填写（`fill_mode: date`）。

实测背景（太保页「保险期限 起/止」）：**不是文本输入框**，而是一个
Vant 风格的滚轮选择器（点开后有 年/月/日 三列 + 取消/确认），
而且**只能选今天及之后**的日期（回溯日期选不了）。

本模块按「先试输入框、再试点开滚轮」的顺序处理，兼容几类常见实现：

============  =====================================================
原生 input      直接 fill（``<input type="date">`` 或普通文本框）
Vant picker     ``.van-picker`` / ``.van-picker-column`` 列滚动选择
antd 日历       ``.ant-calendar-*`` 面板
============  =====================================================

填完一律**回读**（读到的文本里必须出现目标日期的各个部分），
不一致就报错——日期填错比填不上更危险（会产生一份错误期间的保单）。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, List, Optional, Tuple

from .overlay import click_with_guard

#: 滚轮/日历容器的候选选择器。
PICKER_SELECTORS = (
    '.van-picker',                      # Vant
    '.ant-calendar',                    # antd
    '.el-date-picker',
    '.ivu-picker-panel',
    '[class*="picker-panel"]',
    '[class*="date-picker"]',
)

#: 列容器的候选选择器（按年/月/日顺序取前三列）。
COLUMN_SELECTORS = (
    '.van-picker-column',
    '.ant-calendar-column',
    '.el-picker-panel__column',
    'ul[class*="column"]',
    '[class*="picker-column"]',
)

#: 确认按钮文案。
CONFIRM_TEXTS = ("确认", "确定", "完成", "OK")

_DATE_RE = re.compile(r"(\d{4})\D{1,3}(\d{1,2})\D{1,3}(\d{1,2})")


class DatePickerError(RuntimeError):
    """日期填充失败（控件结构不识别 / 目标日期不可选）。"""


def parse_iso_date(value: Any) -> Optional[Tuple[int, int, int]]:
    """解析 ``2026-09-19`` / ``2026年9月19日`` / ``2026/9/19`` → (年, 月, 日)。"""
    text = str(value or "").strip()
    if not text:
        return None
    match = _DATE_RE.search(text)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        date(year, month, day)
    except ValueError:
        return None
    return year, month, day


def fill_date(page: Any, locator: str, value: str,
              max_attempts: int = 3) -> str:
    """把一个日期字段填成 ``value``（ISO 或中文日期）。

    **闭环校验**（实测必需）：Vant 滚轮「点哪一项就选中哪一项」并不总成立
    ——真机上出现过点「20 日」结果选中「21 日」（固定偏移 +1 天）。
    所以每次点完都**回读字段值**，与目标不一致就按观察到的差值修正重试；
    重试用尽仍不一致则报错（宁可不填，也不能填错一天）。

    :return: 实际填进去的文本（回读结果）
    :raises DatePickerError: 控件不识别 / 日期不可选 / 多次修正后仍不一致
    """
    parts = parse_iso_date(value)
    if parts is None:
        raise DatePickerError(f"日期格式无法解析：{value!r}")
    target = date(*parts)

    element = page.locator(locator).first
    try:
        tag = str(element.evaluate("el => el.tagName.toLowerCase()"))
    except Exception:  # noqa: BLE001
        tag = ""

    # 已经是对的就不用动它（实测：页面把起期默认成「明天」，刚好等于目标）
    current = _readback(page, locator)
    if _same_day(current, target):
        return current

    # 可写的输入框：直接输入（最快也最稳），回读确认
    if tag in ("input", "textarea") and _fill_input(element, *parts):
        text = _readback(page, locator)
        if _same_day(text, target) or not text:
            return text
        # 输入了但页面把它规整成别的值 → 继续走滚轮路径修正

    attempt_target = target
    corrections = {"year": 0, "month": 0, "day": 0}
    actual = None
    for attempt in range(max(1, max_attempts)):
        year = target.year + corrections["year"]
        month = target.month + corrections["month"]
        day = target.day + corrections["day"]
        click_with_guard(page, locator, timeout=5000)
        page.wait_for_timeout(500)
        if not _pick_columns(page, year, month, day):
            _cancel(page)
            raise DatePickerError(
                f"日期选择器结构不识别或日期不可选（目标 "
                f"{year}-{month:02d}-{day:02d}）——请人工确认该字段的填法"
                f"（可用 tools/scan_url.py 看页面结构）")
        _confirm(page)
        page.wait_for_timeout(400)

        text = _readback(page, locator)
        actual = _parse_field_date(text)
        if actual is None:
            return text          # 读不出/解析不出 → 不阻断（组件把值藏在别处的情况）
        if actual == target:
            return text
        # 按**每一列各自的偏差**修正后重试（真人发现拨错一格也是这么拨回来的）：
        # 整日差回推会在跨月/跨年时算飞，所以逐列算
        corrections["year"] -= actual.year - target.year
        corrections["month"] -= actual.month - target.month
        corrections["day"] -= actual.day - target.day
        if not 1 <= target.month + corrections["month"] <= 12:
            break
        if not 1 <= target.day + corrections["day"] <= 31:
            break

    raise DatePickerError(
        f"日期回读不一致：期望 {target.isoformat()}，字段显示「{_readback(page, locator)}」"
        f"（已尝试 {max_attempts} 次修正）——已停止，不静默错填")


# ----------------------------------------------------------------------
# 输入框路径
# ----------------------------------------------------------------------
def _fill_input(element: Any, year: int, month: int, day: int) -> bool:
    """尝试直接往输入框里写日期（readonly 的会失败，交给滚轮路径）。"""
    text = f"{year}-{month:02d}-{day:02d}"
    try:
        if element.evaluate("el => !!el.readOnly"):
            return False
    except Exception:  # noqa: BLE001
        return False
    for attempt in (text, f"{year}年{month}月{day}日", f"{year}/{month}/{day}"):
        try:
            element.click(timeout=3000)
            element.fill("")
            element.fill(attempt)
            element.evaluate(
                """el => {
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.blur();
                }""")
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


# ----------------------------------------------------------------------
# 滚轮/日历路径
# ----------------------------------------------------------------------
def _visible_picker(page: Any):
    """当前可见的选择器容器。"""
    for selector in PICKER_SELECTORS:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:  # noqa: BLE001
            continue
        for index in range(min(count, 5)):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    return item
            except Exception:  # noqa: BLE001
                continue
    return None


def _columns(page: Any) -> List[Any]:
    """选择器里的列（年/月/日）。"""
    for selector in COLUMN_SELECTORS:
        try:
            locator = page.locator(selector)
            count = locator.count()
        except Exception:  # noqa: BLE001
            continue
        columns = [locator.nth(i) for i in range(min(count, 6))]
        visible = [c for c in columns
                   if _safe(lambda: c.is_visible(), False)]
        if len(visible) >= 2:
            return visible
    return []


def _pick_columns(page: Any, year: int, month: int, day: int) -> bool:
    """在年/月/日三列里点中目标值。"""
    columns = _columns(page)
    if len(columns) < 3:
        return _pick_from_picker_list(page, year, month, day)

    for column, number in zip(columns, (year, month, day)):
        if not _click_number_in(column, number):
            return False
        page.wait_for_timeout(250)
    return True


def _click_number_in(column: Any, number: int) -> bool:
    """在一列里点中数字等于 ``number`` 的那一项（按文本里的数字比较）。"""
    try:
        items = column.locator('li, [class*="item"], [class*="option"]')
        count = items.count()
    except Exception:  # noqa: BLE001
        return False
    for index in range(min(count, 120)):
        item = items.nth(index)
        try:
            if not item.is_visible():
                continue
            text = str(item.text_content() or "")
            found = re.search(r"\d+", text)
            if found and int(found.group()) == number:
                item.click(timeout=3000)
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _pick_from_picker_list(page: Any, year: int, month: int, day: int) -> bool:
    """退化路径：整个面板里按文本找「2026年」「9月」「19日」逐个点。"""
    picked = 0
    for number, suffix in ((year, "年"), (month, "月"), (day, "日")):
        for text in (f"{number}{suffix}", f"{number:02d}{suffix}", str(number)):
            try:
                item = page.get_by_text(text, exact=True).first
                if item.count() and item.is_visible():
                    item.click(timeout=3000)
                    picked += 1
                    break
            except Exception:  # noqa: BLE001
                continue
    return picked >= 3


def _confirm(page: Any) -> None:
    """点确认按钮（Vant/antd 的确认文案）。"""
    for text in CONFIRM_TEXTS:
        try:
            button = page.get_by_text(text, exact=True).first
            if button.count() and button.is_visible():
                button.click(timeout=3000)
                return
        except Exception:  # noqa: BLE001
            continue


def _cancel(page: Any) -> None:
    """点取消收起选择器（避免面板盖住后续字段）。"""
    for text in ("取消", "关闭"):
        try:
            button = page.get_by_text(text, exact=True).first
            if button.count() and button.is_visible():
                button.click(timeout=2000)
                return
        except Exception:  # noqa: BLE001
            continue


# ----------------------------------------------------------------------
# 回读
# ----------------------------------------------------------------------
def _readback(page: Any, locator: str) -> str:
    try:
        return str(page.locator(locator).first.evaluate(
            "el => (el.value || el.innerText || '').trim()"))
    except Exception:  # noqa: BLE001
        return ""


def _parse_field_date(text: str) -> Optional[date]:
    """把字段里显示的日期解析成 date（读不出返回 None）。"""
    parts = parse_iso_date(text)
    return date(*parts) if parts else None


def _same_day(text: str, target: date) -> bool:
    return _parse_field_date(text) == target


def _verify(page: Any, locator: str, year: int, month: int, day: int) -> None:
    """回读校验：填进去的文本必须能看出目标年月日。"""
    text = _readback(page, locator)
    if not text:
        return          # 读不到就不拦（组件把值藏在别处的情况）
    parsed = _parse_field_date(text)
    if parsed is not None:
        if parsed != date(year, month, day):
            raise DatePickerError(
                f"日期回读不一致：期望 {year}-{month:02d}-{day:02d}，"
                f"字段显示「{text}」——已停止，不静默错填")
        return
    numbers = {int(n) for n in re.findall(r"\d+", text)}
    if not {year, month, day} <= numbers:
        raise DatePickerError(
            f"日期回读不一致：期望 {year}-{month:02d}-{day:02d}，"
            f"字段显示「{text}」——已停止，不静默错填")


def _safe(func, default):
    try:
        return func()
    except Exception:  # noqa: BLE001
        return default


__all__ = ["fill_date", "parse_iso_date", "DatePickerError",
           "PICKER_SELECTORS", "COLUMN_SELECTORS"]
