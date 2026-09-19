"""下拉选择的填法与选项抓取（设计文档附录 C.2 / C.3）。

同样是「下拉」，实现方式不同填法完全不同：

| 类型 | 特征 | 填充方式 |
|---|---|---|
| **原生 `<select>`** | HTML 里就是 ``<select><option>`` | 按选项文本/值 select_option |
| **自定义组件**（更常见） | ``div`` 模拟的下拉（antd / Element 等），真实 select 被隐藏或不存在 | 点击触发框 → 等选项渲染 → 点击目标项；长列表还需滚动或先输入过滤 |

**硬规则（C.3）**：下拉要求精确命中选项文本，**匹配不上不猜**——
填了错误的值可能产生一份错误的保单。归一化后仍找不到就抛错，
并把「页面上实际有哪些选项」带进错误信息，方便补别名表。

本模块同时供 :mod:`batch.probe`（页面探针）复用：选项抓取逻辑只写一份。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

#: 常见自定义下拉的选项选择器（按命中优先级排列）。
CUSTOM_OPTION_SELECTORS = (
    '[role="option"]',
    '.ant-select-item-option',                  # antd v4
    '.ant-select-item',                         # antd v4（无 option 修饰时）
    '.ant-select-dropdown-menu-item',           # antd v3
    '.el-select-dropdown__item',
    '.el-option',
    '.ivu-select-item',
    '.van-picker-column__item',                 # Vant picker（移动端选择器）
    '.van-picker-column li',
    '.select-option',
    '.dropdown-item',
    'ul[role="listbox"] li',
    'ul.select-list li',
    'li[role="option"]',
)

#: 自定义下拉的「展开触发」候选（当传入的 locator 不是触发框本身时兜底）。
#: 实测（太保页 = antd v3）：点 ``div.ant-select`` 根节点不一定展开，
#: 要点 ``.ant-select-selection`` 才算点到选择框。
CUSTOM_TRIGGER_SELECTORS = (
    '.ant-select-selector',                     # antd v4
    '.ant-select-selection',                    # antd v3
    '.el-input',                                # Element UI
    '.ivu-select-selection',                    # iView
    '[role="combobox"]',
    'input',                                    # 兜底：聚焦输入框也能展开
)

#: 长下拉的搜索框候选（先输入过滤再选）。
SEARCH_INPUT_SELECTORS = (
    'input.ant-select-selection-search-input',
    'input.ant-select-search__field',           # antd v3
    '.el-select input',
    '.ivu-select input',
    'input[role="combobox"]',
)

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "（）【】《》，。：；！？　",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "()[]<>,.:;!? ",
)


def normalize_option(text: Any) -> str:
    """选项文本归一化：去空白（含全角空格）+ 全角转半角。

    与 batch.dictionaries.normalize 同一套口径——两边必须一致，
    否则「选项里有、却匹配不上」会变成难以排查的假异常。
    """
    if text is None:
        return ""
    return re.sub(r"[\s\u3000]+", "", str(text).translate(_FULLWIDTH)).strip()


#: 收集原生 select 的选项（含 value 与文本）。
NATIVE_OPTIONS_JS = """(sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    if (el.tagName.toLowerCase() !== 'select') return null;
    return [...el.options].map(o => ({
        value: o.value,
        text: (o.textContent || '').trim(),
        disabled: !!o.disabled,
    }));
}"""

#: 收集自定义下拉的可见选项文本（用于探针与「找不到时给出实际选项」）。
CUSTOM_OPTIONS_JS = """(selectors) => {
    const items = [];
    for (const sel of selectors) {
        for (const el of document.querySelectorAll(sel)) {
            if (el.offsetParent === null) continue;
            const text = (el.textContent || '').trim();
            if (!text) continue;
            items.push({text: text, selector: sel});
        }
        if (items.length) break;   // 用第一个命中的选择器族，避免同一列表重复收集
    }
    return items;
}"""

#: 「这个下拉的面板在哪」——返回一个**只属于它**的选择器（找不到返回 null）。
#:
#: 实测教训：页面上同时有多个下拉（邮箱、证件类型、学生类别…），
#: 全页找 ``[role="option"]`` 会把别的下拉的候选项当成本字段的选项
#: （把「大中专学校学生」当输入值喂给邮箱框，收上来一堆 xxx@163.com）。
#: 优先用 ``aria-owns`` / ``aria-controls`` 的关联关系；否则选与触发框
#: 水平重叠最多、且在它下方的那个可见面板，并做上临时标记。
PANEL_SCOPE_JS = """(el) => {
    const MARK = 'data-fp-panel';
    document.querySelectorAll('[' + MARK + ']').forEach(n => n.removeAttribute(MARK));
    const visible = n => {
        const s = getComputedStyle(n), r = n.getBoundingClientRect();
        return r.height > 10 && r.width > 10 && s.display !== 'none'
            && s.visibility !== 'hidden' && +s.opacity > 0;
    };
    const link = node => {
        if (!node || !node.getAttribute) return null;
        const id = node.getAttribute('aria-owns') || node.getAttribute('aria-controls');
        if (!id) return null;
        const panel = document.getElementById(id);
        return panel && visible(panel) ? panel : null;
    };
    const nodes = [el, el.querySelector('input'), el.querySelector('[aria-owns]'),
                   el.querySelector('[aria-controls]')].filter(Boolean);
    for (const node of nodes) {
        const panel = link(node);
        if (panel) { panel.setAttribute(MARK, '1'); return '[' + MARK + '="1"]'; }
    }
    const r = el.getBoundingClientRect();
    let best = null, bestScore = 0;
    const panelSel = '.ant-select-dropdown, .el-select-dropdown, .ivu-select-dropdown,'
                   + ' [role="listbox"], ul.select-list, .van-popup, .van-picker';
    for (const panel of document.querySelectorAll(panelSel)) {
        if (!visible(panel)) continue;
        if (panel.closest('[class*="dropdown"]') && panel.closest('[class*="dropdown"]') !== panel) continue;
        const pr = panel.getBoundingClientRect();
        const overlap = Math.min(r.right, pr.right) - Math.max(r.left, pr.left);
        const score = (overlap > 0 ? overlap : 0) + (pr.top >= r.top - 20 ? 1000 : 0);
        if (score > bestScore) { bestScore = score; best = panel; }
    }
    if (!best) return null;
    best.setAttribute(MARK, '1');
    return '[' + MARK + '="1"]';
}"""

#: 在指定面板内收集可见选项。
OPTIONS_IN_SCOPE_JS = """([scope, selectors]) => {
    const root = scope ? document.querySelector(scope) : document;
    if (!root) return [];
    const items = [];
    for (const sel of selectors) {
        for (const el of root.querySelectorAll(sel)) {
            if (el.offsetParent === null) continue;
            const text = (el.textContent || '').trim();
            if (!text) continue;
            items.push({text: text, selector: sel});
        }
        if (items.length) break;
    }
    return items;
}"""


class DropdownError(RuntimeError):
    """下拉填充失败（选项不存在 / 组件结构不识别）。"""


def _native_options(page: Any, locator: str) -> Optional[List[dict]]:
    """按 locator 读原生 <select> 的选项；不是 select 返回 None。

    用 locator 求值（而不是 ``document.querySelector``）才能正确处理
    ``>> nth=`` 这类链式语法。
    """
    try:
        return page.locator(locator).first.evaluate(
            """el => {
                if (!el || el.tagName.toLowerCase() !== 'select') return null;
                return [...el.options].map(o => ({
                    value: o.value, text: (o.textContent || '').trim(),
                    disabled: !!o.disabled,
                }));
            }""")
    except Exception:  # noqa: BLE001
        return None


def is_native_select(page: Any, locator: str) -> bool:
    """locator 指向的是不是原生 <select>。"""
    return _native_options(page, locator) is not None


def collect_options(page: Any, locator: str) -> Dict[str, Any]:
    """抓取一个下拉的全部选项（页面探针 P-1 的核心）。

    :return: ``{"kind": "native"|"custom"|"unknown", "options": [文本...],
               "raw": [...], "error": 失败原因（可选）}``
    """
    native = _native_options(page, locator)
    if native:
        return {
            "kind": "native",
            "options": [o["text"] for o in native if not o.get("disabled")],
            "raw": native,
        }

    # 自定义组件：先点击展开，再收集可见选项
    opened = False
    last_error: Optional[Exception] = None
    for trigger in _trigger_candidates(locator):
        try:
            _click_with_guard(page, trigger, timeout=3000)
            opened = True
            break
        except Exception as e:  # noqa: BLE001
            last_error = e
            continue
    if not opened:
        # 兜底：JS 直接派发点击事件（实测有些 H5 对 Playwright 的真实点击
        # 会判定为被遮挡/intercepted，但组件本身只监听 DOM 事件）
        for trigger in _trigger_candidates(locator):
            if _js_click(page, trigger):
                opened = True
                break
    if not opened:
        return {"kind": "unknown", "options": [], "raw": [],
                "error": (str(last_error) if last_error else "点击展开失败")[:300]}

    page.wait_for_timeout(500)
    scope = panel_scope(page, locator)
    items = _options_in_scope(page, scope)
    if not items:
        # 鼠标点击没出列表：移动端 H5 常把展开绑在**触摸事件**上，补一次 tap
        for trigger in _trigger_candidates(locator):
            if _try_tap(page, trigger):
                page.wait_for_timeout(600)
                scope = panel_scope(page, locator)
                items = _options_in_scope(page, scope)
                if items:
                    break
    texts = _unique([i["text"] for i in items])
    _try_close(page)
    return {"kind": "custom", "options": texts, "raw": items, "scope": scope or ""}


def _try_tap(page: Any, locator: str) -> bool:
    """用触摸事件点一下（移动端 H5 有些组件只认 touch）。"""
    from .overlay import dismiss_blocking_popups

    dismiss_blocking_popups(page, rounds=4, interval_seconds=1.0)
    try:
        page.locator(locator).first.tap(timeout=4000)
        return True
    except Exception:  # noqa: BLE001 — 非触摸上下文/被遮挡时静默失败
        return False


def panel_scope(page: Any, locator: str) -> Optional[str]:
    """该下拉的选项面板选择器（只属于它；找不到返回 None）。"""
    try:
        return page.locator(locator).first.evaluate(PANEL_SCOPE_JS)
    except Exception:  # noqa: BLE001
        return None


def _options_in_scope(page: Any, scope: Optional[str]) -> List[dict]:
    """收集面板内的可见选项；没有 scope 时退回全页搜索（旧行为）。"""
    if scope:
        try:
            return page.evaluate(OPTIONS_IN_SCOPE_JS,
                                 [scope, list(CUSTOM_OPTION_SELECTORS)]) or []
        except Exception:  # noqa: BLE001
            return []
    try:
        return page.evaluate(CUSTOM_OPTIONS_JS, list(CUSTOM_OPTION_SELECTORS)) or []
    except Exception:  # noqa: BLE001
        return []


def _click_with_guard(page: Any, locator: str, timeout: int = 5000) -> None:
    """点击（先关掉挡路的弹窗；被拦截时关掉再重试一次）。"""
    from .overlay import click_with_guard

    click_with_guard(page, locator, timeout=timeout)


def _js_click(page: Any, locator: str) -> bool:
    """用 JS 直接派发点击（绕过 Playwright 的可点击性检查）。"""
    from .overlay import click_js

    return click_js(page, locator)


def select_value(page: Any, locator: str, value: str,
                 timeout_rounds: int = 12) -> str:
    """把一个下拉选中为 ``value``（原生 select 或自定义组件）。

    :return: 实际选中的文本（用于回读比对）
    :raises DropdownError: 选项里没有这个值（归一化后仍无）→ 不猜，直接报错
    """
    target = normalize_option(value)
    if not target:
        raise DropdownError("下拉值为空")

    native = _native_options(page, locator)
    if native:
        return _select_native(page, locator, target, native)

    return _select_custom(page, locator, target, timeout_rounds=timeout_rounds)


def _select_native(page: Any, locator: str, target: str,
                   options: List[dict]) -> str:
    """原生 <select>：按文本命中（归一化比较），否则报错。"""
    for option in options:
        if option.get("disabled"):
            continue
        if normalize_option(option["text"]) == target:
            page.locator(locator).first.select_option(option["value"])
            return option["text"]
    for option in options:
        if normalize_option(option["value"]) == target:
            page.locator(locator).first.select_option(option["value"])
            return option["text"]
    available = "、".join(o["text"] for o in options if not o.get("disabled"))
    raise DropdownError(
        f"下拉中没有选项「{target}」；页面实际选项：{available or '（空）'}"
        f"——请核对页面选项字典/别名表（不猜）")


def _select_custom(page: Any, locator: str, target: str,
                   timeout_rounds: int = 12) -> str:
    """自定义组件：点击展开 → （必要时在**本字段的**搜索框里过滤）→ 点目标项。

    三条防御（都是实测踩出来的）：
      1. 面板**已经展开**时直接点目标项，不再点一次触发框
      2. 选项查找与输入过滤都**限定在本字段的面板/组件内**——
         否则会把别的下拉（实测：邮箱框）的候选当成本字段的选项
      3. 选完**回读触发框文本**，与目标值不一致就报错（不静默错选）
    """
    if _find_visible_option(page, locator, target, timeout_rounds=1) is not None:
        return _click_option(page, locator, target)

    last_error: Optional[Exception] = None
    for trigger in _trigger_candidates(locator):
        # 先收起残留面板、关掉可能重弹的遮罩：面板可能盖住触发框，
        # 点击会被拦截或落到别的元素上（实测该页「点了又弹」）
        _try_close(page)
        try:
            _click_with_guard(page, trigger, timeout=5000)
        except Exception as e:  # noqa: BLE001
            last_error = e
            continue
        page.wait_for_timeout(300)

        # 先在本字段的面板里找；找不到再（必要时）用它自己的搜索框过滤
        hit = _find_visible_option(page, locator, target,
                                   timeout_rounds=max(3, timeout_rounds // 3))
        if hit is None:
            if _try_type_search(page, locator, target):
                hit = _find_visible_option(page, locator, target,
                                           timeout_rounds=timeout_rounds)
        if hit is not None:
            hit.click()
            page.wait_for_timeout(300)
            _try_close(page)
            return _verify_selected(page, locator, target)

        options = collect_visible_options(page, locator)
        _try_close(page)
        raise DropdownError(
            f"自定义下拉中没有选项「{target}」；当前可见选项："
            f"{'、'.join(options[:20]) or '（空）'}——不猜，请先核对选项字典")
    raise DropdownError(f"下拉展开失败：{locator}（{last_error}）")


def _click_option(page: Any, locator: str, target: str) -> str:
    """面板已在展开状态：直接点击匹配的选项并回读。"""
    hit = _find_visible_option(page, locator, target, timeout_rounds=6)
    if hit is None:
        raise DropdownError(f"下拉中没有可见选项「{target}」——不猜")
    hit.click()
    page.wait_for_timeout(300)
    _try_close(page)
    return _verify_selected(page, locator, target)


def _verify_selected(page: Any, locator: str, target: str) -> str:
    """回读触发框文本，确认真的选中了目标值（读不到文本时跳过校验）。

    注意用**完整 locator**（含 ``>> nth=`` 这类链式语法）——用去掉链式的
    CSS 会读到同 class 的另一个元素（实测：回读到了「证件类型」的文本）。
    """
    try:
        text = page.locator(locator).first.evaluate(
            "el => (el.value || el.textContent || '').trim()")
    except Exception:  # noqa: BLE001
        return target
    text = str(text or "")
    if text and normalize_option(target) not in normalize_option(text):
        raise DropdownError(
            f"选择后回读不一致：期望「{target}」，触发框实际显示「{text}」"
            f"——已停止，不静默错选")
    return target


def collect_visible_options(page: Any, locator: str = "") -> List[str]:
    """当前可见的自定义下拉选项文本。

    给了 ``locator`` 就只看**该字段自己的面板**（实测：全页找会把别的
    下拉的候选混进来，比如把「大中专学校学生」当输入值喂给邮箱框后
    收上来一堆 ``xxx@163.com``）。
    """
    scope = panel_scope(page, locator) if locator else None
    items = _options_in_scope(page, scope)
    return _unique([i["text"] for i in items])


def _find_visible_option(page: Any, locator: str, target: str,
                         timeout_rounds: int = 12):
    """轮询找出文本与目标值（归一化后）相等的可见选项。

    查找范围限定在该下拉自己的面板内（``panel_scope``）。
    """
    scope = panel_scope(page, locator)
    scope_locator = page.locator(scope) if scope else None
    for _ in range(max(1, timeout_rounds)):
        for selector in CUSTOM_OPTION_SELECTORS:
            try:
                root = scope_locator if scope_locator is not None else page
                found = root.locator(selector)
                count = found.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(min(count, 200)):
                item = found.nth(index)
                try:
                    if not item.is_visible():
                        continue
                    if normalize_option(item.text_content()) == target:
                        return item
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(250)
    return None


def _try_type_search(page: Any, locator: str, target: str) -> bool:
    """长下拉：在**本字段自己的**搜索框里输入关键字过滤。

    只在目标项没出现在可见选项里时才调用（避免无谓输入污染别的输入框）。
    """
    base = _css_of(locator)
    for selector in SEARCH_INPUT_SELECTORS:
        for candidate in (f"{base} {selector}", f"{base} >> {selector}"):
            try:
                item = page.locator(candidate).first
                if item.count() == 0 or not item.is_visible():
                    continue
                if item.evaluate("el => !!el.readOnly"):
                    continue
                item.click(timeout=3000)
                item.fill(target)
                page.wait_for_timeout(400)
                return True
            except Exception:  # noqa: BLE001
                continue
    return False


def _try_close(page: Any) -> None:
    """收起下拉面板（Escape；某些组件不认就点一下空白处）。

    面板不收起会**遮住后面要操作的元素**（实测：级联抓取时年级面板挡住
    学生类别的选项，点击被拦截）。
    """
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    except Exception:  # noqa: BLE001
        pass
    if not collect_visible_options(page):
        return
    try:
        # 组件不响应 Escape 时，模拟「点空白处收起」
        page.evaluate("() => document.body.click()")
        page.wait_for_timeout(200)
    except Exception:  # noqa: BLE001
        pass


def _trigger_candidates(locator: str) -> List[str]:
    """触发框候选：先试传入的 locator，再试常见组件的触发元素。"""
    candidates = [locator]
    base = locator.split(" >> ")[0]
    for selector in CUSTOM_TRIGGER_SELECTORS:
        candidates.append(f"{base} {selector}")
        candidates.append(f"{base} >> {selector}")
    return candidates


def _css_of(locator: str) -> str:
    """取 CSS 部分（去掉 Playwright 的 ``>> nth=N`` 等链式语法）。"""
    return locator.split(" >> ")[0].strip()


def _unique(values: List[str]) -> List[str]:
    seen: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
    return seen
