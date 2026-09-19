"""页面探针（P-1）：抓下拉选项作为字典权威值（设计文档附录 C，含实测修正）。

**为什么必须先做这一步**：年级/班级在投保页面上是**下拉选择**，值必须
**精确命中选项文本**——归纳出来的名字和页面选项对不上就选不中。
所以权威值是页面选项，台账的 N 种写法只是要映射过去的脏数据。

**真实页面实测（2026-09-19，修正了附录 C.1 的假设）**：

- **学校名称是文本输入**（placeholder「请录入学校名称」），不是下拉 ——
  没有选项集合，规范名只能人工确认（见 ``dictionary.kinds.school: text``）
- **年级 / 班级是下拉**，且**年级的选项随「学生类别」变化**
  （学生类别：大中专学校学生 / 中学生 / 小学生）
- 班级选项形如 ``1班 / 2班 / …``

所以探针除了「展开下拉抓选项」，还要：
  1. 认出**文本输入**字段（报告里写清，不当作失败）
  2. 遇到级联（``depends_on``）：先抓上级下拉（学生类别）的全部选项，
     再**逐个选择**上级选项、抓下级（年级/班级）在该类别下的选项集合

选项抓取逻辑复用 :mod:`page.dropdown`（那里同时是 filler 的填法实现，
**只写一份**，探针抓什么、filler 就按什么选）。

本模块不碰浏览器之外的东西：页面由调用方打开（tools/probe_options.py
复用现有的「二维码 → URL → Playwright 打开」流程），便于单独测试。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from page.dropdown import (CUSTOM_OPTION_SELECTORS, collect_options,
                           normalize_option, select_value)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")

#: 字段期望的控件类型。
KIND_CHOICE = "choice"    # 下拉选择（必须有选项集合）
KIND_ANY = "any"          # 下拉或文本输入都可（按页面实际报告）

#: 结果里的控件类型取值。
WIDGET_NATIVE = "native"
WIDGET_CUSTOM = "custom"
WIDGET_TEXT = "text"

#: 下拉里不算「可选值」的占位项（抓选项时保留，级联时跳过）。
_PLACEHOLDER_OPTIONS = ("", "请选择", "请选择…", "请选择...", "--", "-")

#: 字段 key → 命令行参数名。
CLI_FLAGS = {"school": "school", "grade": "grade", "class_name": "class",
             "student_level": "select-level"}


@dataclass(frozen=True)
class ProbeField:
    """一个要探测的字段。"""

    key: str                 # 字典名（school / grade / class_name）
    label: str               # 页面上的字段名（学校名称 / 年级 / 班级）
    options_file: str        # 产出的选项文件
    aliases_file: str        # 配套的别名表
    keywords: Tuple[str, ...] = ()   # 自动定位时用于匹配标签的关键词
    kind: str = KIND_CHOICE          # 期望的控件类型
    depends_on: str = ""             # 级联的上级字段 key（如 student_level）

    @property
    def keywords_all(self) -> Tuple[str, ...]:
        return self.keywords or (self.label,)


#: 三个字典字段（顺序即探测顺序）。
PROBE_FIELDS: Tuple[ProbeField, ...] = (
    # 实测：学校名称是 div 模拟的文本控件（没有下拉）→ kind=any，抓到什么报什么。
    # 关键词必须**精确**：「大中专学校学生」里也含「学校」，用「学校」会误命中学生类别
    ProbeField("school", "学校名称", "options_school.yaml", "school_aliases.yaml",
               ("学校名称", "就读学校"), kind=KIND_ANY),
    ProbeField("grade", "年级", "options_grade.yaml", "grade_aliases.yaml",
               ("年级", "就读年级"), depends_on="student_level"),
    ProbeField("class_name", "班级", "options_class.yaml", "class_aliases.yaml",
               ("班级", "所在班级"), depends_on="student_level"),
)

#: 级联的上级下拉（不是字典字段，但必须抓出来才知道年级/班级有哪些选项）。
SELECTOR_FIELDS: Tuple[ProbeField, ...] = (
    ProbeField("student_level", "学生类别", "options_student_level.yaml", "",
               ("学生类别", "学生级别", "就读类别")),
)

ALL_FIELDS: Tuple[ProbeField, ...] = PROBE_FIELDS + SELECTOR_FIELDS

#: 定位器的来源（报告里要写清是配置给的还是自动识别的）。
SOURCE_PRODUCT = "product"   # 品种 yaml 的 field_mapping
SOURCE_AUTO = "auto"         # 页面自动识别
SOURCE_MANUAL = "manual"     # 命令行指定


@dataclass
class FieldProbeResult:
    """一个字段的探测结果。"""

    field: ProbeField
    locator: str = ""
    source: str = ""
    kind: str = "unknown"              # native | custom | text | unknown
    options: List[str] = field(default_factory=list)
    #: 级联分组：{上级选项: 该类别下的选项列表}（如 {"小学生": ["1年级", ...]}）
    by_selector: Dict[str, List[str]] = field(default_factory=dict)
    note: str = ""
    error: str = ""
    candidates: List[dict] = field(default_factory=list)  # 定位歧义时的候选
    #: 页面上该元素的属性（自动识别时带上：tag / dropdownish / placeholder …）
    element: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return self.kind == WIDGET_TEXT

    @property
    def ok(self) -> bool:
        if not self.locator or self.error:
            return False
        if self.is_text:
            return True          # 文本输入：没有选项，但探针任务完成
        return bool(self.options)

    @property
    def kind_label(self) -> str:
        return {WIDGET_NATIVE: "原生 <select>",
                WIDGET_CUSTOM: "自定义组件",
                WIDGET_TEXT: "文本输入（无下拉）"}.get(self.kind, "未识别")

    def summary_line(self) -> str:
        if self.error:
            line = (f"{self.field.label}：✗ {self.error}"
                    + (f"（locator：{self.locator}）" if self.locator else ""))
            detail = self.element_detail()
            if detail:
                line += f"\n      元素：{detail}"
            return line
        if self.is_text:
            line = f"{self.field.label}：文本输入（无下拉选项）← {self.locator}"
        elif not self.options:
            line = f"{self.field.label}：✗ 未抓到选项（控件类型 {self.kind_label}）" \
                   f"← {self.locator}"
        else:
            line = (f"{self.field.label}：{len(self.options)} 个选项"
                    f"（{self.kind_label}）← {self.locator}")
        if self.note:
            line += f"｜注意：{self.note}"
        return line

    def element_detail(self) -> str:
        """页面上该元素的属性摘要（定位失败时用于诊断）。"""
        el = self.element or {}
        if not el:
            return ""
        bits = [f"tag={el.get('tag', '?')}",
                f"下拉特征={'有' if el.get('dropdownish') else '无'}"]
        if el.get("placeholder"):
            bits.append(f"placeholder={el['placeholder']}")
        if el.get("aria"):
            bits.append(f"aria-label={el['aria']}")
        if el.get("labels"):
            bits.append("labels=" + " / ".join(el["labels"][:2]))
        return "，".join(bits)

    def group_lines(self) -> List[str]:
        """级联分组明细：每个上级选项下的选项数量与内容。"""
        lines: List[str] = []
        for group, options in self.by_selector.items():
            preview = "、".join(options[:8]) + ("…" if len(options) > 8 else "")
            lines.append(f"{group} → {len(options)} 个：{preview}")
        return lines


# ----------------------------------------------------------------------
# 定位：品种配置 → 自动识别
# ----------------------------------------------------------------------
def locators_from_product(product: Any,
                          fields: Sequence[ProbeField] = ALL_FIELDS
                          ) -> Dict[str, Tuple[str, str]]:
    """从品种的 field_mapping 里取字段的 locator。

    :return: ``{字段 key: (locator, property 名)}``；没配就不出现
    """
    found: Dict[str, Tuple[str, str]] = {}
    for mapping in getattr(product, "field_mapping", []) or []:
        prop = normalize_option(getattr(mapping, "property", ""))
        for field in fields:
            if field.key in found:
                continue
            if prop in {normalize_option(k) for k in field.keywords_all}:
                found[field.key] = (mapping.locator, mapping.property)
    return found


#: 页面里收集「字段候选」：原生 select + 常见自定义组件 + 文本输入。
#:
#: 实测要点（太保页 = antd）：
#:   - locator 生成优先级： #id → [placeholder=] → [aria-label=] → 稳定 class → nth-of-type
#:     （nth-of-type 路径在页面重渲染后容易失效）
#:   - **同一 class 会命中多个元素**（多个 div.ant-select），必须带唯一标识
#:     （用 path 判重，再按 DOM 顺序加 ``>> nth=N`` 消歧）
#:   - ``dropdownish``：元素里是否有下拉的痕迹（箭头 / selector / combobox），
#:     用来区分「真下拉」和「长得像下拉的文本控件」（实测：div.school-select）
#:   - 返回前按 DOM 顺序排序，保证 nth 索引稳定
_DETECT_JS = """() => {
    const norm = t => (t || '').replace(/[\\s\\u3000]+/g, '');
    const esc = s => String(s).replace(/\\\\/g, '\\\\\\\\').replace(/"/g, '\\\\"');
    const COMPONENTS = ['.ant-select', '.el-select', '.ivu-select', '[role="combobox"]'];
    const stableClass = el => {
        const cls = (el.className && typeof el.className === 'string') ? el.className : '';
        for (const name of cls.split(/\\s+/)) {
            if (!name) continue;
            if (/[0-9a-f]{5,}/i.test(name)) continue;
            if (/(select|picker|input|field|combobox|box)$/i.test(name)) return name;
        }
        return '';
    };
    const cssPath = el => {
        const parts = [];
        let node = el;
        let guard = 0;
        while (node && node.nodeType === 1 && guard < 8) {
            guard += 1;
            if (node.id) { parts.unshift('#' + node.id); break; }
            let part = node.tagName.toLowerCase();
            const parent = node.parentElement;
            if (parent) {
                const same = [...parent.children].filter(c => c.tagName === node.tagName);
                if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
            }
            parts.unshift(part);
            node = parent;
        }
        return parts.join(' > ');
    };
    const locatorOf = el => {
        if (el.id) return '#' + el.id;
        const ph = el.getAttribute('placeholder');
        if (ph) return el.tagName.toLowerCase() + '[placeholder="' + esc(ph) + '"]';
        const aria = el.getAttribute('aria-label');
        if (aria) return '[aria-label="' + esc(aria) + '"]';
        const cls = stableClass(el);
        if (cls) return el.tagName.toLowerCase() + '.' + cls;
        return cssPath(el);
    };
    const depthOf = el => {
        let d = 0, node = el;
        while (node && node.parentElement) { d += 1; node = node.parentElement; }
        return d;
    };
    const dropdownishOf = el => {
        if (el.tagName && el.tagName.toLowerCase() === 'select') return true;
        for (const sel of COMPONENTS) { if (el.matches && el.matches(sel)) return true; }
        return !!el.querySelector(
            '[role="combobox"], .ant-select-selector, .ant-select-selection,'
            + ' .el-input, .ivu-select-selection, .ant-select-arrow,'
            + ' ul[role="listbox"], [role="option"], .ant-select-dropdown,'
            + ' i[aria-label^="icon: down"], .anticon-down');
    };
    const labelsOf = el => {
        const bits = [];
        const push = v => {
            const t = norm(v).slice(0, 40);
            if (t && !bits.some(b => b.slice(0, 12) === t.slice(0, 12))) bits.push(t);
        };
        push(el.getAttribute('aria-label'));
        push(el.getAttribute('placeholder'));
        const ref = el.getAttribute('aria-labelledby');
        if (ref) { const t = document.getElementById(ref); if (t) push(t.textContent); }
        if (el.labels) { for (const l of el.labels) push(l.textContent); }
        let sib = el.previousElementSibling;
        if (sib) push(sib.textContent);
        if (el.parentElement) push(el.parentElement.textContent);
        return bits;
    };
    const out = [];
    const add = (el, kind) => {
        if (!el || el.disabled) return;
        if (kind === 'input') {
            const type = (el.type || 'text').toLowerCase();
            if (['hidden', 'file', 'checkbox', 'radio', 'submit', 'button'].includes(type)) return;
        }
        // 组件内的元素统一用组件根（下拉组件要点它的根才能展开）
        let root = null;
        for (const sel of COMPONENTS) {
            const found = el.closest(sel);
            if (found && found.offsetParent !== null) { root = found; break; }
        }
        const target = root || el;
        out.push({
            _el: target,
            kind: kind,
            root: root === el,                    // 元素本身就是组件根（antd 的 div.ant-select）
            component: root ? String(root.className) : '',
            tag: target.tagName.toLowerCase(),
            locator: locatorOf(target),
            path: cssPath(target),
            depth: depthOf(target),
            dropdownish: dropdownishOf(target),
            labels: labelsOf(target),
            placeholder: target.getAttribute('placeholder') || '',
            aria: target.getAttribute('aria-label') || '',
            text: norm(target.textContent).slice(0, 40),
        });
    };
    for (const el of document.querySelectorAll('select')) add(el, 'native');
    const custom = '[role="combobox"], .ant-select, .el-select, .ivu-select,'
                 + ' [class*="select"], [class*="picker"]';
    for (const el of document.querySelectorAll(custom)) {
        if (el.offsetParent === null) continue;
        add(el, 'custom');
    }
    for (const el of document.querySelectorAll('input, textarea')) {
        if (el.offsetParent === null) continue;
        add(el, 'input');
    }
    // 按 DOM 顺序排序（nth 消歧依赖顺序稳定）
    out.sort((a, b) => (a._el.compareDocumentPosition(b._el) & 4) ? -1 : 1);
    return out.map(item => {
        const copy = Object.assign({}, item);
        delete copy._el;
        return copy;
    });
}"""


def detect_locators(page: Any, product: Any = None,
                    fields: Sequence[ProbeField] = PROBE_FIELDS
                    ) -> Dict[str, FieldProbeResult]:
    """识别字段的 locator（品种配置优先，其次页面自动识别）。

    自动识别**不猜**：同一字段命中多个不同 locator 时报歧义，
    由调用方提示人工用 ``--school/--grade/--class`` 指定；
    下拉字段只认到文本输入时报错（说明页面结构与预期不符）。
    """
    results: Dict[str, FieldProbeResult] = {
        field.key: FieldProbeResult(field=field) for field in fields}

    configured = locators_from_product(product, fields) if product is not None else {}
    for key, (locator, prop) in configured.items():
        results[key].locator = locator
        results[key].source = SOURCE_PRODUCT
        results[key].note = f"来自品种配置（属性「{prop}」）"

    missing = [field for field in fields if not results[field.key].locator]
    if not missing:
        return results

    candidates = _collect_candidates(page)
    for field in missing:
        matched = _pick_candidate(candidates, field)
        if isinstance(matched, list):       # 歧义：多个候选
            results[field.key].candidates = matched
            results[field.key].error = (
                f"页面上找到 {len(matched)} 个候选，无法确定哪个是"
                f"「{field.label}」——请用 --{CLI_FLAGS[field.key]} \"<locator>\" 指定")
            continue
        if matched is None:
            results[field.key].error = (
                f"页面上未找到「{field.label}」——请用 "
                f"--{CLI_FLAGS[field.key]} \"<locator>\" 指定")
            continue
        if field.kind == KIND_CHOICE and matched.get("dropdownish") is False:
            results[field.key].locator = matched["locator"]
            results[field.key].element = dict(matched)
            results[field.key].error = (
                f"「{field.label}」在页面上没有下拉特征（无箭头/selector/combobox），"
                f"看起来是文本类控件——与预期不符，请人工确认页面结构")
            continue
        results[field.key].locator = matched["locator"]
        results[field.key].element = dict(matched)
        results[field.key].source = SOURCE_AUTO
        keywords = [normalize_option(k) for k in field.keywords_all]
        hit_labels = [str(l) for l in (matched.get("labels") or [])
                      if any(k in normalize_option(l) for k in keywords)]
        if matched.get("kind") == "input" or matched.get("dropdownish") is False:
            results[field.key].kind = WIDGET_TEXT
        hint = min(hit_labels, key=len) if hit_labels else ""
        results[field.key].note = (f"自动识别（页面标签：{hint}）" if hint
                                   else "自动识别（页面标签不明确，请人工核对）")
    return results


#: 弹窗处理复用 page/overlay（点击前统一把关；实测该页弹窗「点了又弹」）。
from page.overlay import (dismiss_blocking_popups, format_popup_report,
                          has_blocking_overlay)

#: 兼容旧名（CLI 与测试都在用 ``probe.dismiss_popups``）。
dismiss_popups = dismiss_blocking_popups


def _collect_candidates(page: Any) -> List[dict]:
    """页面上的字段候选。

    两步处理（实测必需）：
      1. 按 ``path`` 去重（同一元素可能被多个选择器命中）
      2. 同一 ``locator`` 命中多个元素时（如 3 个 ``div.ant-select``），
         按 DOM 顺序加 ``>> nth=N`` 消歧——否则年级/班级会被前三者中的
         第一个冒充，甚至整条丢失
    """
    try:
        items = page.evaluate(_DETECT_JS) or []
    except Exception:  # noqa: BLE001 — 页面结构不识别时由调用方提示手动指定
        return []

    unique: Dict[str, dict] = {}
    ordered: List[dict] = []
    for item in items:
        path = str(item.get("path") or item.get("locator") or "")
        if not path:
            continue
        exist = unique.get(path)
        if exist is None:
            unique[path] = item
            ordered.append(item)
        elif len(item.get("labels") or []) > len(exist.get("labels") or []):
            exist["labels"] = item.get("labels") or exist.get("labels")

    counts: Dict[str, int] = {}
    for item in ordered:
        locator = str(item.get("locator") or "")
        counts[locator] = counts.get(locator, 0) + 1
    for item in ordered:
        locator = str(item.get("locator") or "")
        if counts.get(locator, 0) > 1:
            # 同一个 class 命中多个元素（如多个 div.ant-select）→ 改用唯一 path。
            # 不用 >> nth=N：页面重渲染后元素顺序会变，N 会指向别的字段
            # （实测：nth=2 指到了「证件类型」，回读时才发现选错）
            item["locator"] = str(item.get("path") or locator)
    return ordered


def _pick_candidate(candidates: Sequence[dict], field: ProbeField) -> Optional[Any]:
    """给字段挑候选：命中标签且评分最高的一个；同分多个 → 返回列表（歧义）。

    评分顺序（实测逐步补出来的）：
      1. 标签出现位置（越靠前越可信）
      2. **是不是真下拉**（有箭头/selector/combobox）——antd 的
         ``div.ant-select`` 与其内层占位 div 标签相同，必须优先选组件根
      3. **元素本身就是组件根**（``div.ant-select`` 而非它内部的 div）
      4. 控件偏好（原生 select / 自定义组件 优先于普通输入框）
      5. 深度（同分取最内层）——学校名称有 4 层嵌套 div 带同一句 placeholder，
         真正能填的是最内层
    """
    keywords = [normalize_option(k) for k in field.keywords_all]
    hits: List[Tuple[int, int, int, int, dict]] = []
    for item in candidates:
        labels = [normalize_option(l) for l in (item.get("labels") or [])]
        for index, label in enumerate(labels):
            if any(key in label for key in keywords):
                dropdown = 0 if item.get("dropdownish") else 1
                root = 0 if item.get("root") else 1
                prefer = 0 if item.get("kind") in ("native", "custom") else 1
                depth = -int(item.get("depth") or 0)      # 越深（内层）越小 → 排前面
                hits.append((index, dropdown, root, prefer, depth, item))
                break
    if not hits:
        return None
    hits.sort(key=lambda h: h[:5])
    best = hits[0]
    same_score = [h for h in hits if h[:5] == best[:5]]
    if len(same_score) > 1 and len({h[5].get("locator") for h in same_score}) > 1:
        return [h[5] for h in same_score]     # 同分多个且不是同一元素 → 歧义，不猜
    return best[5]


# ----------------------------------------------------------------------
# 抓选项
# ----------------------------------------------------------------------
def _element_tag(page: Any, locator: str) -> str:
    """locator 指向元素的 tagName（识别文本输入 / 下拉）。"""
    try:
        return str(page.locator(locator).first.evaluate(
            "el => el.tagName.toLowerCase()")).lower()
    except Exception:  # noqa: BLE001
        return ""


def _collect(page: Any, locator: str) -> Dict[str, Any]:
    """抓一次下拉选项（不改动任何结果对象）。"""
    return collect_options(page, locator)


def probe_field(page: Any, result: FieldProbeResult) -> FieldProbeResult:
    """抓一个字段（原地更新 result）：文本控件 → 记类型；下拉 → 抓选项。

    判定顺序（实测：太保页的「学校名称」是 ``div.school-select``，
    里面没有 input，也没有下拉特征）：
      1. 元素就是 input/textarea → 文本输入
      2. 元素没有下拉特征（无箭头/selector/combobox）→ 文本类控件；
         预期是下拉的字段（KIND_CHOICE）在这一步报错，不硬当下拉点
      3. 否则按下拉抓（原生 select / 自定义组件）
    """
    if not result.locator:
        return result

    element = result.element or {}
    tag = str(element.get("tag") or "")
    if not tag:
        tag = _element_tag(page, result.locator)

    if tag in ("input", "textarea"):
        return _mark_text_field(result)

    dropdownish = element.get("dropdownish")
    if dropdownish is None:
        dropdownish = _is_dropdownish(page, result.locator)
    if dropdownish is False:
        return _mark_text_field(result, fake=tag not in ("input", "textarea"))

    # 试着抓选项 —— 即使是 combobox 也点一次：
    # 实测「学校名称」按 class 像「可输入+联想」，但要**点开看才知道**
    # 有没有候选列表（使用者反馈它更像点选）
    try:
        info = _collect(page, result.locator)
    except Exception as e:  # noqa: BLE001 — 单个字段失败不影响其他字段
        result.error = f"展开下拉失败：{e}"
        return result

    result.kind = str(info.get("kind") or "unknown")
    result.options = [str(o) for o in (info.get("options") or []) if str(o).strip()]

    if result.options:
        if result.kind == "unknown":
            result.kind = WIDGET_CUSTOM
        if result.kind == WIDGET_CUSTOM:
            scroll_note = _scroll_note(page)
            if scroll_note:
                result.note = (result.note + "｜" if result.note else "") + scroll_note
        return result

    # 没抓到选项：combobox / 允许文本的字段按文本类处理（不是失败），
    # 预期是下拉的字段才报错
    if _is_combobox(element) or result.field.kind != KIND_CHOICE:
        result.note = (result.note + "｜" if result.note else "") + \
            "点开后没有出现候选列表"
        return _mark_text_field(result, combobox=_is_combobox(element),
                                keep_note=True)
    detail = str(info.get("error") or "").strip()
    result.error = ("控件类型未识别（既不是原生 select，也点不开自定义组件）"
                    + (f"：{detail}" if detail else "")
                    + "——请人工确认 locator")
    return result


def _is_combobox(element: Dict[str, Any]) -> bool:
    """是不是「可输入 + 联想」的下拉（antd combobox / 原生 datalist）。

    实测：太保页的「学校名称」是 ``ant-select-combobox``——能打字、
    会有联想候选，但没有固定选项列表，所以按文本类处理（规范名人工确认）。
    """
    classes = f"{element.get('component', '')} {element.get('cls', '')}"
    return "combobox" in classes.lower()


def _mark_text_field(result: FieldProbeResult, fake: bool = False,
                     combobox: bool = False,
                     keep_note: bool = False) -> FieldProbeResult:
    """记为文本类控件（没有固定下拉选项）。"""
    result.kind = WIDGET_TEXT
    result.options = []
    if result.field.kind == KIND_CHOICE:
        what = "可输入+联想的输入框" if combobox else \
            ("div 模拟的输入框" if fake else "文本输入")
        result.error = (f"该字段在页面上是{what}，没有固定下拉选项"
                        f"——与预期不符，请人工确认页面结构")
        return result
    if combobox:
        note = "可输入 + 联想（combobox）：点开后没有固定选项列表"
    elif fake:
        note = "文本类控件（div 模拟的输入框，无下拉选项）"
    else:
        note = "文本输入（无下拉选项）"
    if result.field.key == "school":
        note += "，规范名需人工确认（config/school_aliases.yaml）"
    if keep_note and result.note:
        result.note = f"{result.note}｜{note}"
    else:
        result.note = note
    return result


def _is_dropdownish(page: Any, locator: str) -> bool:
    """元素内部有没有下拉的痕迹（原生 select / 箭头 / selector / combobox）。"""
    try:
        return bool(page.locator(locator).first.evaluate(
            """el => {
                if (el.tagName && el.tagName.toLowerCase() === 'select') return true;
                if (el.matches && el.matches('.ant-select, .el-select, .ivu-select, [role="combobox"]')) return true;
                return !!el.querySelector(
                    '[role="combobox"], .ant-select-selector, .ant-select-selection,'
                    + ' .el-input, .ivu-select-selection, .ant-select-arrow,'
                    + ' ul[role="listbox"], [role="option"], .ant-select-dropdown,'
                    + ' i[aria-label^="icon: down"], .anticon-down');
            }"""))
    except Exception:  # noqa: BLE001
        return True      # 判断不了时按下拉试（filler 会给出明确报错）


def probe_all(page: Any, product: Any = None,
              overrides: Optional[Dict[str, str]] = None,
              interval_seconds: float = 0.8,
              cascade: bool = True,
              dismiss: bool = True,
              preflight: Optional[List[Dict[str, Any]]] = None) -> Dict[str, FieldProbeResult]:
    """探测所有字段（关弹窗 + 定位 + 抓选项 + 级联展开）。

    :param overrides: ``{字段 key: locator}``，命令行 ``--school`` 等传入
    :param cascade: 是否展开级联——逐个选择「学生类别」的每个选项，
        分别抓该类别下的年级/班级选项（实测年级选项随学生类别变化）
    :param dismiss: 先关掉挡住表单的引导弹窗（实测必需，否则点击全被拦截）
    :param preflight: 传入列表时，把弹窗处理结果追加进去供调用方报告
    :param interval_seconds: 每次页面操作之间的间隔——投保站点有访问频率
        限制，按「正常人工节奏」逐个展开，不并发冲击
    """
    import time

    if dismiss:
        info = dismiss_popups(page, interval_seconds=max(0.3, interval_seconds))
        if preflight is not None:
            preflight.append(info)

    results = detect_locators(page, product)
    for key, locator in (overrides or {}).items():
        if key in results and locator:
            results[key].locator = locator
            results[key].source = SOURCE_MANUAL
            results[key].error = ""
            results[key].candidates = []
            results[key].note = "命令行指定"

    ordered = {field.key: results[field.key] for field in PROBE_FIELDS}

    # 1) 不依赖上级的字段（学校：文本输入）
    for result in ordered.values():
        if not result.field.depends_on and not (result.error and not result.locator):
            probe_field(page, result)

    dependent = [r for r in ordered.values()
                 if r.field.depends_on and not (r.error and not r.locator)]
    selector_key = dependent[0].field.depends_on if dependent else ""
    selector = None
    if dependent and cascade:
        selector = _probe_selector(page, product, selector_key, overrides,
                                   interval_seconds)

    if selector is not None and selector.options:
        groups = [o for o in selector.options if normalize_option(o) not in
                  {normalize_option(p) for p in _PLACEHOLDER_OPTIONS}]
        if groups:
            _probe_cascading(page, dependent, selector, groups, interval_seconds)
        else:
            for result in dependent:
                probe_field(page, result)
    else:
        # 没有级联（未找到上级 / 命令行 --no-cascade）：单次抓取
        for index, result in enumerate(dependent):
            if index:
                time.sleep(max(0.0, interval_seconds))
            probe_field(page, result)

    if selector is not None:
        ordered["student_level"] = selector
    return ordered


def _probe_selector(page: Any, product: Any, selector_key: str,
                    overrides: Optional[Dict[str, str]],
                    interval_seconds: float) -> Optional[FieldProbeResult]:
    """抓级联的上级下拉（学生类别）本身。"""
    if not selector_key:
        return None
    field = next((f for f in SELECTOR_FIELDS if f.key == selector_key), None)
    if field is None:
        return None
    results = detect_locators(page, product, (field,))
    result = results[field.key]
    manual = (overrides or {}).get(field.key)
    if manual:
        result.locator = manual
        result.source = SOURCE_MANUAL
        result.error = ""
        result.note = "命令行指定"
    if result.error and not result.locator:
        return result
    _ = interval_seconds
    return probe_field(page, result)


def _probe_cascading(page: Any, dependent: Sequence[FieldProbeResult],
                     selector: FieldProbeResult, groups: Sequence[str],
                     interval_seconds: float) -> None:
    """逐个选择上级选项，抓下级字段在该类别下的选项（并合并成全集）。"""
    import time

    current = _selected_value(page, selector.locator)
    for group_index, group in enumerate(groups):
        if group_index:
            time.sleep(max(0.0, interval_seconds))
        if normalize_option(group) == normalize_option(current):
            pass      # 已经是当前值：不用再点一次（点了反而可能选不上）
        else:
            try:
                select_value(page, selector.locator, group)
            except Exception as e:  # noqa: BLE001 — 选不上就跳过这一组，不猜
                selector.note = _join_note(
                    selector.note, f"无法选择「{group}」（{e}），该组未抓取")
                continue
        page.wait_for_timeout(int(max(0.0, interval_seconds) * 1000) or 300)
        for result in dependent:
            try:
                info = _collect(page, result.locator)
            except Exception as e:  # noqa: BLE001
                result.error = result.error or f"展开下拉失败：{e}"
                continue
            options = [str(o) for o in (info.get("options") or []) if str(o).strip()]
            options = [o for o in options
                       if normalize_option(o) not in
                       {normalize_option(p) for p in _PLACEHOLDER_OPTIONS}]
            if not options:
                continue
            result.kind = result.kind if result.kind in (WIDGET_NATIVE, WIDGET_CUSTOM) \
                else str(info.get("kind") or "unknown")
            result.by_selector[group] = options
            for option in options:
                if option not in result.options:
                    result.options.append(option)

    for result in dependent:
        if result.options and not result.error:
            result.note = _join_note(
                result.note,
                f"选项随「{selector.field.label}」变化（已按 "
                f"{len(result.by_selector)} 个类别分别抓取）")
        elif not result.options and not result.error:
            result.error = f"在「{selector.field.label}」各选项下都没抓到选项"


def _selected_value(page: Any, locator: str) -> str:
    """读下拉当前选中的文本（用于跳过「重新选择当前值」的多余点击）。"""
    try:
        return str(page.locator(locator).first.evaluate(
            """el => {
                const node = el.querySelector(
                    '.ant-select-selection-selected-value, .ant-select-selection__rendered,'
                    + ' .el-select__selected-item');
                return ((node ? node.innerText : el.innerText) || '').trim();
            }""") or "")
    except Exception:  # noqa: BLE001
        return ""


def _join_note(note: str, extra: str) -> str:
    return f"{note}｜{extra}" if note else extra


def _scroll_note(page: Any) -> str:
    """自定义下拉的列表若可滚动，选项可能没渲染全（虚拟列表）。"""
    try:
        info = page.evaluate(
            """(selectors) => {
                for (const sel of selectors) {
                    const items = [...document.querySelectorAll(sel)]
                        .filter(e => e.offsetParent !== null);
                    if (!items.length) continue;
                    const box = items[0].parentElement || items[0];
                    return {scrollable: box.scrollHeight > box.clientHeight + 4,
                            count: items.length};
                }
                return {scrollable: false, count: 0};
            }""", list(CUSTOM_OPTION_SELECTORS))
    except Exception:  # noqa: BLE001
        return ""
    if info and info.get("scrollable"):
        return "选项列表可滚动，可能有未渲染的选项（虚拟列表），需人工核对完整性"
    return ""


# ----------------------------------------------------------------------
# 写文件
# ----------------------------------------------------------------------
def write_options_file(path: str, result: FieldProbeResult,
                       product_name: str = "", url: str = "",
                       probed_at: str = "") -> str:
    """把选项写入 ``options_*.yaml``（保留说明头 + 探测元信息）。

    程序只读 ``options`` 列表（级联字段读 ``probe.by_selector`` 分组）；
    ``probe`` 段其余内容供排查。
    """
    header = _render_header(result, product_name, url, probed_at)
    meta: Dict[str, Any] = {
        "product": product_name,
        "url": url,
        "widget": result.kind,
        "locator": result.locator,
        "source": result.source,
        "count": len(result.options),
        "probed_at": probed_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    if result.by_selector:
        meta["note"] = "选项随上级下拉变化，by_selector 为各分组下的选项"
        meta["by_selector"] = {k: list(v) for k, v in result.by_selector.items()}
    payload = {"probe": meta, "options": list(result.options)}
    body = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=200)
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + body)
    return path


def _render_header(result: FieldProbeResult, product_name: str,
                   url: str, probed_at: str) -> str:
    field = result.field
    stamp = probed_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if result.is_text:
        what = (f"# ⚠ 实测：该字段是**文本输入**（没有下拉选项），本文件空着是正常的；\n"
                f"#    规范名请在 config/{field.aliases_file} 里人工确认后填写。\n")
        rules = (f"# 匹配规则（dictionary.kinds.{field.key}: text）：\n"
                 f"#   页面上没有选项集合，权威值 = 人工确认的规范名（别名表目标值）；\n"
                 f"#   未显式映射的写法一律进「数据异常」——不猜。\n")
    else:
        what = ""
        rules = (f"# 匹配规则（dictionary.kinds.{field.key}: choice）：\n"
                 f"#   值必须精确命中下面的选项文本；别名表目标值也必须落在集合内，\n"
                 f"#   否则进「数据异常」——不猜、不填相近值。\n")
    cascade = ""
    if result.by_selector:
        cascade = ("# 注意：本字段的选项随上级下拉（如「学生类别」）变化，\n"
                   "#      probe.by_selector 记录了各分组下的选项，options 是全集。\n")
    return f"""# {field.label}的权威选项（页面探针产出，**不要手改**）
#
# 由 tools/probe_options.py 打开投保页面、抓取该字段后写下。
# 字典的权威来源（设计文档附录 C.1）：先抓页面，再映射台账写法
# （config/{field.aliases_file}），不是反过来。
#
{what}{cascade}{rules}#
# 抓取结果（{stamp}）：
#   品种：{product_name or '（未指定）'}
#   控件类型：{result.kind_label}   选项数：{len(result.options)}
#   locator：{result.locator}
#
# 程序只读下面的 options 列表；probe 段供排查，可忽略。
# 注意：重新探测会覆盖本文件。

"""


# ----------------------------------------------------------------------
# 别名表核对（不猜：目标值必须落在权威集合内）
# ----------------------------------------------------------------------
def load_kinds(config_dir: str = _DEFAULT_CONFIG_DIR) -> Dict[str, str]:
    """读 ``config/batch.yaml`` 的 ``dictionary.kinds``（字段控件类型）。"""
    data = _load_yaml(os.path.join(config_dir, "batch.yaml"))
    kinds = (data.get("dictionary") or {}).get("kinds") or {}
    return {str(k): str(v) for k, v in kinds.items()} if isinstance(kinds, dict) else {}


def check_alias_targets(options_by_key: Dict[str, Sequence[str]],
                        config_dir: str = _DEFAULT_CONFIG_DIR,
                        kinds: Optional[Dict[str, str]] = None,
                        notes: Optional[List[str]] = None) -> List[str]:
    """检查别名表的目标值是否都在权威集合内，返回问题清单。

    - 下拉字段（choice）：目标值必须落在页面选项集合内，否则报问题
    - 文本字段（text）：没有选项可核对 → 只做提示（进 ``notes``），
      除非别名表为空（那意味着该字段永远无法通过校验，属于问题）
    """
    from .dictionaries import load_dictionary_set

    problems: List[str] = []
    if kinds is None:
        kinds = load_kinds(config_dir)
    dictionary_set = load_dictionary_set(config_dir, kinds=kinds)
    for field in PROBE_FIELDS:
        dictionary = dictionary_set.get(field.key)
        aliases = dictionary.aliases if dictionary else {}
        if dictionary is not None and dictionary.is_text_field:
            if aliases:
                message = (f"{field.label}（文本输入）：{len(aliases)} 条人工确认的"
                           f"规范名（没有选项可自动核对，请人工 review "
                           f"{field.aliases_file}）")
                (notes if notes is not None else problems).append(message)
            else:
                problems.append(
                    f"{field.label}（文本输入）：别名表为空 → 所有写法都会进"
                    f"「数据异常」；请人工确认规范名后填入 {field.aliases_file}")
            continue
        options = options_by_key.get(field.key) or []
        if not options:
            if aliases:
                problems.append(
                    f"{field.label}（{field.options_file} 为空）：别名表里 "
                    f"{len(aliases)} 条目标值没有选项可核对 —— 导入时这些写法"
                    f"会全部进「数据异常」（不猜）")
            continue
        index = {normalize_option(o) for o in options}
        for key, target in aliases.items():
            if normalize_option(target) not in index:
                problems.append(
                    f"{field.label}：「{key}」→「{target}」不在选项集合内"
                    f"（别名表需人工修正）")
    return problems


def load_probed_options(config_dir: str = _DEFAULT_CONFIG_DIR
                        ) -> Dict[str, List[str]]:
    """读回所有 ``options_*.yaml``（用于 `--check` 与导入报告）。"""
    result: Dict[str, List[str]] = {}
    for field in ALL_FIELDS:
        result[field.key] = _load_options_file(
            os.path.join(config_dir, field.options_file))
    return result


def load_by_selector(config_dir: str = _DEFAULT_CONFIG_DIR
                     ) -> Dict[str, Dict[str, List[str]]]:
    """读回级联分组（``probe.by_selector``）：{字段 key: {上级选项: [选项]}}。"""
    out: Dict[str, Dict[str, List[str]]] = {}
    for field in PROBE_FIELDS:
        path = os.path.join(config_dir, field.options_file)
        data = _load_yaml(path)
        groups = ((data.get("probe") or {}).get("by_selector") or {}) \
            if isinstance(data, dict) else {}
        if isinstance(groups, dict) and groups:
            out[field.key] = {str(k): [str(o) for o in (v or [])]
                              for k, v in groups.items()}
    return out


def _load_options_file(path: str) -> List[str]:
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return []
    return [str(o) for o in (data.get("options") or [])]


def _load_yaml(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001
        return {}


# ----------------------------------------------------------------------
# 报告
# ----------------------------------------------------------------------
def format_report(results: Dict[str, FieldProbeResult],
                  product_name: str = "",
                  dump_candidates: Sequence[dict] = ()) -> str:
    """控制台报告：每个字段的选项数量 + 控件类型（设计文档 C.1 要求）。"""
    lines = ["", "=" * 62,
             f"字段探测结果{'（品种：' + product_name + '）' if product_name else ''}",
             "=" * 62]
    for field in ALL_FIELDS:
        result = results.get(field.key)
        if result is None:
            continue
        lines.append("  " + result.summary_line())
        for group_line in result.group_lines():
            lines.append(f"      · {group_line}")
        for candidate in result.candidates[:6]:
            lines.append(f"      · 候选 {candidate.get('locator')}"
                         f"（{candidate.get('kind')}；"
                         f"{' / '.join((candidate.get('labels') or [])[:2]) or '无标签'}）")
    if dump_candidates:
        lines += ["", "候选元素明细（诊断用）：",
                  f"  {'kind':<7}{'tag':<8}{'locator':<46}labels"]
        for item in dump_candidates:
            locator = str(item.get("locator") or "")
            if len(locator) > 44:
                locator = locator[:41] + "…"
            labels = " / ".join((item.get("labels") or [])[:2])
            lines.append(f"  {item.get('kind', ''):<7}{item.get('tag', ''):<8}"
                         f"{locator:<46}{labels}")
    return "\n".join(lines)


def field_mapping_snippet(results: Dict[str, FieldProbeResult]) -> str:
    """生成可直接粘进品种 yaml 的 field_mapping 片段。"""
    lines = ["  # —— 就读信息（实测：学校是文本输入，年级/班级是下拉）——"]
    for field in PROBE_FIELDS:
        result = results.get(field.key)
        if result is None or not result.locator or result.error:
            continue
        if result.is_text:
            lines += [
                f"  - property: {field.label}",
                f"    locator: '{result.locator}'",
                "    required: true",
            ]
        else:
            lines += [
                f"  - property: {field.label}",
                f"    locator: '{result.locator}'",
                "    fill_mode: select           # 原生 select / 自定义组件都由 page/dropdown.py 处理",
                "    required: true",
            ]
    selector = results.get("student_level")
    if selector is not None and selector.locator and not selector.error:
        lines.insert(1, "  # 注意：年级/班级的选项随「学生类别」变化，录入时需先选它")
        lines.insert(2, f"  #       学生类别 locator: '{selector.locator}'")
    return "\n".join(lines)


def collect_candidates(page: Any) -> List[dict]:
    """公开的候选收集（诊断用：--dump-candidates）。"""
    return _collect_candidates(page)


__all__ = ["PROBE_FIELDS", "SELECTOR_FIELDS", "ALL_FIELDS", "ProbeField",
           "FieldProbeResult", "probe_all", "probe_field", "detect_locators",
           "locators_from_product", "write_options_file", "check_alias_targets",
           "load_probed_options", "load_by_selector", "format_report",
           "field_mapping_snippet", "collect_candidates", "SOURCE_PRODUCT",
           "SOURCE_AUTO", "SOURCE_MANUAL", "KIND_CHOICE", "KIND_ANY",
           "WIDGET_TEXT", "WIDGET_NATIVE", "WIDGET_CUSTOM"]
