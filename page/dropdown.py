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
    '.ant-select-item-option',
    '.el-select-dropdown__item',
    '.el-option',
    '.ivu-select-item',
    '.select-option',
    '.dropdown-item',
    'ul[role="listbox"] li',
    'ul.select-list li',
    'li[role="option"]',
)

#: 自定义下拉的「展开触发」候选（当传入的 locator 不是触发框本身时兜底）。
CUSTOM_TRIGGER_SELECTORS = (
    '.ant-select-selector',
    '.el-select',
    '.ivu-select',
    '[role="combobox"]',
)

#: 长下拉的搜索框候选（先输入过滤再选）。
SEARCH_INPUT_SELECTORS = (
    'input.ant-select-selection-search-input',
    '.el-select input',
    '.ivu-select input',
    'input[role="combobox"]',
)

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "（）【】《》，。、：；！？　",
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


class DropdownError(RuntimeError):
    """下拉填充失败（选项不存在 / 组件结构不识别）。"""


def is_native_select(page: Any, locator: str) -> bool:
    """locator 指向的是不是原生 <select>。"""
    try:
        return page.evaluate(NATIVE_OPTIONS_JS, _css_of(locator)) is not None
    except Exception:  # noqa: BLE001
        return False


def collect_options(page: Any, locator: str) -> Dict[str, Any]:
    """抓取一个下拉的全部选项（页面探针 P-1 的核心）。

    :return: ``{"kind": "native"|"custom"|"unknown", "options": [文本...],
               "raw": [...]}``
    """
    native = None
    try:
        native = page.evaluate(NATIVE_OPTIONS_JS, _css_of(locator))
    except Exception:  # noqa: BLE001
        native = None
    if native:
        return {
            "kind": "native",
            "options": [o["text"] for o in native if not o.get("disabled")],
            "raw": native,
        }

    # 自定义组件：先点击展开，再收集可见选项
    opened = False
    for trigger in _trigger_candidates(locator):
        try:
            page.locator(trigger).first.click(timeout=3000)
            opened = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not opened:
        return {"kind": "unknown", "options": [], "raw": []}

    page.wait_for_timeout(500)
    items = page.evaluate(CUSTOM_OPTIONS_JS, list(CUSTOM_OPTION_SELECTORS))
    texts = _unique([i["text"] for i in items])
    _try_close(page)
    return {"kind": "custom", "options": texts, "raw": items}


def select_value(page: Any, locator: str, value: str,
                 timeout_rounds: int = 12) -> str:
    """把一个下拉选中为 ``value``（原生 select 或自定义组件）。

    :return: 实际选中的文本（用于回读比对）
    :raises DropdownError: 选项里没有这个值（归一化后仍无）→ 不猜，直接报错
    """
    target = normalize_option(value)
    if not target:
        raise DropdownError("下拉值为空")

    native = None
    try:
        native = page.evaluate(NATIVE_OPTIONS_JS, _css_of(locator))
    except Exception:  # noqa: BLE001
        native = None

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
            page.locator(_css_of(locator)).select_option(option["value"])
            return option["text"]
    for option in options:
        if normalize_option(option["value"]) == target:
            page.locator(_css_of(locator)).select_option(option["value"])
            return option["text"]
    available = "、".join(o["text"] for o in options if not o.get("disabled"))
    raise DropdownError(
        f"下拉中没有选项「{target}」；页面实际选项：{available or '（空）'}"
        f"——请核对页面选项字典/别名表（不猜）")


def _select_custom(page: Any, locator: str, target: str,
                   timeout_rounds: int = 12) -> str:
    """自定义组件：点击展开 → （必要时输入过滤/滚动）→ 点击目标项。"""
    last_error: Optional[Exception] = None
    for trigger in _trigger_candidates(locator):
        try:
            page.locator(trigger).first.click(timeout=3000)
        except Exception as e:  # noqa: BLE001
            last_error = e
            continue
        page.wait_for_timeout(300)

        # 长下拉：若有搜索框，先输入过滤（避免选项分页/需搜索才出）
        _try_type_search(page, target)

        hit = _find_visible_option(page, target, timeout_rounds=timeout_rounds)
        if hit is None and not _try_type_search(page, target):
            hit = _find_visible_option(page, target, timeout_rounds=timeout_rounds)
        if hit is not None:
            hit.click()
            page.wait_for_timeout(300)
            _try_close(page)
            return target

        options = collect_visible_options(page)
        _try_close(page)
        raise DropdownError(
            f"自定义下拉中没有选项「{target}」；当前可见选项："
            f"{'、'.join(options[:20]) or '（空）'}——不猜，请先核对选项字典")
    raise DropdownError(f"下拉展开失败：{locator}（{last_error}）")


def collect_visible_options(page: Any) -> List[str]:
    """当前页面上可见的自定义下拉选项文本。"""
    try:
        items = page.evaluate(CUSTOM_OPTIONS_JS, list(CUSTOM_OPTION_SELECTORS))
    except Exception:  # noqa: BLE001
        return []
    return _unique([i["text"] for i in items])


def _find_visible_option(page: Any, target: str, timeout_rounds: int = 12):
    """轮询找出文本与目标值（归一化后）相等的可见选项。"""
    for _ in range(max(1, timeout_rounds)):
        for selector in CUSTOM_OPTION_SELECTORS:
            try:
                locator = page.locator(selector)
                count = locator.count()
            except Exception:  # noqa: BLE001
                continue
            for index in range(min(count, 200)):
                item = locator.nth(index)
                try:
                    if not item.is_visible():
                        continue
                    if normalize_option(item.text_content()) == target:
                        return item
                except Exception:  # noqa: BLE001
                    continue
        page.wait_for_timeout(250)
    return None


def _try_type_search(page: Any, target: str) -> bool:
    """长下拉先输入关键字过滤；返回是否成功输入。"""
    for selector in SEARCH_INPUT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if locator.count() == 0 or not locator.is_visible():
                continue
            locator.fill(target)
            page.wait_for_timeout(400)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _try_close(page: Any) -> None:
    """收起下拉面板（点空白处），避免遮挡后续操作。"""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
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
