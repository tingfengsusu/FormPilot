"""学校 / 年级 / 班级字典与归一化匹配（设计文档 5.1、附录 C）。

**核心约束（附录 C.1）**：投保页面的学校/班级是**下拉选择**，不是文本输入，
值必须**精确命中选项文本**。所以字典的权威来源是**页面下拉里的选项**，
台账里的各种写法是需要映射到权威值的「脏数据」：

    权威值 = 页面下拉选项（options_*.yaml，由页面探针 tools/probe_options.py 产出）
    脏数据 = 台账/OCR 的 N 种写法 → 别名表（*_aliases.yaml，人工确认）

匹配流程（附录 C.3）：

    归一化（去空白/全角转半角/去噪声）→ 查选项 → 查别名
        ↓
    命中唯一 → 用该值；命中多个 → 数据异常（歧义，不猜）；未命中 → 数据异常

**硬规则**：匹配结果必须落在权威集合内，否则一律进「数据异常」，
绝不猜一个相近值填进去——填了错误的值，可能产生一份错误的保单。

**权威集合按控件类型分两种**（``dictionary.kinds`` 配置，实测得出）：

============  ==========================================================
``choice``    下拉选择（年级 / 班级）：权威值 = **页面选项**（options_*.yaml，
              探针产出）。别名目标值必须落在选项集合内，否则数据异常。
``text``      文本输入（学校名称）：页面上没有选项集合，权威值 = **人工确认的
              规范名**（*_aliases.yaml 的目标值）。未显式映射的写法一律数据异常。
============  ==========================================================

**没有「过渡模式」**：下拉字段（choice）在选项尚未探针产出时
（``options_*.yaml`` 为空），别名表的目标值**不会**被当作权威值使用——
那些值没有经过页面核对，放行等于把失败推迟到录入阶段（那里的报错更难排查）。
此时所有下拉字典字段一律判「数据异常」，并明确提示先运行
:mod:`tools.probe_options` 抓选项。文本字段（text）没有选项可探，
规范名只能人工确认——但同样**不猜**：没映射的写法照样报数据异常。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

import yaml

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")

#: 匹配状态。
MATCH_EXACT = "exact"          # 与选项文本完全一致
MATCH_ALIAS = "alias"          # 经别名表命中权威值
MATCH_EMPTY = "empty"          # 原值为空
MATCH_AMBIGUOUS = "ambiguous"  # 归一化后命中多个候选（歧义，不猜）
MATCH_UNMATCHED = "unmatched"  # 未命中任何权威值
#: 别名表目标值不在页面选项集合内（配置错误 → 数据异常）
MATCH_ALIAS_OUT_OF_OPTIONS = "alias_out_of_options"

#: 控件类型：下拉选择（权威值 = 页面选项）。
KIND_CHOICE = "choice"
#: 控件类型：文本输入（页面上没有选项集合，权威值 = 人工确认的规范名）。
KIND_TEXT = "text"

#: 字段 key → 控件类型（默认下拉；由 config/batch.yaml 的 dictionary.kinds 覆盖）。
DEFAULT_KINDS: Dict[str, str] = {
    "school": KIND_CHOICE,
    "grade": KIND_CHOICE,
    "class_name": KIND_CHOICE,
}

#: 归一化时剥离的常见噪声后缀/前缀（仅作可选，默认关闭）。
_NOISE_SUFFIXES = ("学校", "小学", "中学", "高中", "幼儿园", "校区")
_NOISE_PREFIXES = ("洛阳市", "洛阳市区", "洛阳")

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "（）【】《》，。：；！？　",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "()[]<>,.:;!? ",
)


def normalize(text) -> str:
    """归一化：去空白（含全角空格）→ 全角转半角 → 大写化字母。

    注意：**不做**语义缩写展开（"柳小" → "柳集小学" 交给别名表），
    也不做模糊匹配——下拉要求精确命中，猜一个相近值是危险行为。
    """
    if text is None:
        return ""
    value = str(text)
    value = value.translate(_FULLWIDTH)
    value = re.sub(r"[\s\u3000]+", "", value)  # 去掉中间与首尾空白
    return value.strip()


def strip_noise(text: str) -> str:
    """剥离常见前后缀噪声（可选步骤，仅在配置开启时使用）。"""
    value = normalize(text)
    for prefix in _NOISE_PREFIXES:
        if value.startswith(prefix) and len(value) > len(prefix):
            value = value[len(prefix):]
    for suffix in _NOISE_SUFFIXES:
        if value.endswith(suffix) and len(value) > len(suffix):
            value = value[: -len(suffix)]
    return value


@dataclass
class MatchResult:
    """一次字典匹配的结果。"""

    raw: str = ""
    canonical: str = ""                 # 命中的权威值（未命中为空）
    status: str = MATCH_UNMATCHED
    candidates: List[str] = field(default_factory=list)
    authority: str = ""                 # "options"（页面选项）| "none"（无字典）
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (MATCH_EXACT, MATCH_ALIAS)

    @property
    def detail(self) -> str:
        if self.ok:
            return self.note or f"匹配到「{self.canonical}」（页面选项）"
        if self.status == MATCH_EMPTY:
            return "值为空"
        if self.status == MATCH_AMBIGUOUS:
            return self.note or (f"「{self.raw}」同时命中多个候选："
                                 f"{'、'.join(self.candidates)}——歧义，不猜")
        if self.status == MATCH_ALIAS_OUT_OF_OPTIONS:
            return self.note or f"「{self.raw}」的别名目标值不在页面选项集合内"
        return self.note or f"「{self.raw}」不在页面选项/别名表中（需人工确认后补别名）"


class Dictionary:
    """一份字典：权威选项（options）+ 别名表（aliases）。"""

    def __init__(self, name: str,
                 options: Optional[Iterable[str]] = None,
                 aliases: Optional[Dict[str, str]] = None,
                 options_path: str = "",
                 aliases_path: str = "",
                 strip_noise_enabled: bool = False,
                 kind: str = KIND_CHOICE):
        self.name = name
        self.options: List[str] = [str(o) for o in (options or []) if str(o).strip()]
        self.aliases: Dict[str, str] = {
            normalize(k): str(v) for k, v in (aliases or {}).items() if str(k).strip()
        }
        self.options_path = options_path
        self.aliases_path = aliases_path
        self.strip_noise_enabled = strip_noise_enabled
        self.kind = kind if kind in (KIND_CHOICE, KIND_TEXT) else KIND_CHOICE

        #: 选项归一化索引（去重：同归一化值对应多个选项即歧义）
        self._option_index: Dict[str, List[str]] = {}
        for option in self.options:
            self._option_index.setdefault(normalize(option), []).append(option)

    # ------------------------------------------------------------------
    # 载入
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, name: str, options_path: str, aliases_path: str,
             strip_noise_enabled: bool = False,
             kind: str = KIND_CHOICE) -> "Dictionary":
        """从 options / aliases 两个 yaml 载入（文件缺失按空处理）。"""
        options = _load_yaml_list(options_path)
        aliases = _load_yaml_map(aliases_path)
        return cls(name=name, options=options, aliases=aliases,
                   options_path=options_path, aliases_path=aliases_path,
                   strip_noise_enabled=strip_noise_enabled, kind=kind)

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------
    @property
    def is_text_field(self) -> bool:
        """页面上的该字段是不是文本输入（没有选项集合）。"""
        return self.kind == KIND_TEXT

    @property
    def options_ready(self) -> bool:
        """权威值是否可用：下拉=选项已探针产出；文本输入=无需选项。"""
        if self.is_text_field:
            return True
        return bool(self.options)

    @property
    def authority_values(self) -> List[str]:
        """权威值集合。

        - 下拉（choice）：页面选项（下拉是唯一真相，没有退路）
        - 文本输入（text）：人工确认的规范名（别名表目标值）——
          页面上没有选项集合，这是唯一可用的权威来源，且**仍需人工逐条确认**
        """
        if self.options:
            return list(self.options)
        if self.is_text_field:
            seen: List[str] = []
            for target in self.aliases.values():
                if target not in seen:
                    seen.append(target)
            return seen
        return []

    @property
    def authority(self) -> str:
        if self.options:
            return "options"
        return "manual" if self.is_text_field else "options"

    def match(self, text) -> MatchResult:
        """匹配一个台账写法到权威值。

        命中唯一 → ok；命中多个 → 歧义；未命中/别名目标不在选项内 →
        unmatched（数据异常，不猜）。
        """
        raw = "" if text is None else str(text)
        key = normalize(raw)
        if not key:
            return MatchResult(raw=raw, status=MATCH_EMPTY, authority=self.authority,
                               note="值为空")

        if self.is_text_field:
            return self._match_text_field(raw, key)

        # 1) 选项文本直接命中
        candidates = list(self._option_index.get(key, []))
        if candidates:
            return self._result_from_options(raw, candidates)

        # 2) 别名表命中（别名 → 权威值；目标值必须在选项集合内）
        alias_target = self.aliases.get(key)
        if alias_target:
            return self._result_from_alias(raw, alias_target)

        # 3) 去噪声后再试一轮（可选）
        if self.strip_noise_enabled:
            stripped = strip_noise(raw)
            if stripped and stripped != key:
                candidates = list(self._option_index.get(stripped, []))
                if candidates:
                    return self._result_from_options(raw, candidates)
                target = self.aliases.get(stripped)
                if target:
                    return self._result_from_alias(raw, target)

        return self._unmatched(raw)

    # ------------------------------------------------------------------
    # 内部：命中判定
    # ------------------------------------------------------------------
    def _match_text_field(self, raw: str, key: str) -> MatchResult:
        """文本输入字段：只有「人工确认过的写法」才放行（不猜）。"""
        target = self.aliases.get(key)
        if not target and self.strip_noise_enabled:
            target = self.aliases.get(strip_noise(raw))
        if target:
            return MatchResult(
                raw=raw, canonical=target, status=MATCH_ALIAS,
                candidates=[target], authority=self.authority,
                note=f"经人工确认：「{raw}」→「{target}」（页面为文本输入）")
        # 值本身已是规范名（导入时已映射过一次，校验层再匹配时就是这个值）
        if normalize(raw) in {normalize(v) for v in self.aliases.values()}:
            return MatchResult(
                raw=raw, canonical=raw, status=MATCH_EXACT,
                candidates=[raw], authority=self.authority,
                note=f"「{raw}」已是人工确认的规范名")
        return MatchResult(
            raw=raw, status=MATCH_UNMATCHED, authority=self.authority,
            note=(f"「{raw}」没有映射到规范名（页面该字段是**文本输入**，"
                  f"没有下拉选项可核对）——请在 {self._aliases_filename()} 里"
                  f"人工确认后显式映射；不猜"))

    def _result_from_options(self, raw: str, candidates: List[str]) -> MatchResult:
        unique = _unique(candidates)
        if len(unique) > 1:
            return MatchResult(raw=raw, status=MATCH_AMBIGUOUS, candidates=unique,
                               authority=self.authority)
        return MatchResult(raw=raw, canonical=unique[0], status=MATCH_EXACT,
                           candidates=unique, authority=self.authority)

    def _result_from_alias(self, raw: str, target: str) -> MatchResult:
        """别名命中：目标值必须落在页面选项集合内（硬规则）。"""
        if not self.options:
            return MatchResult(
                raw=raw, status=MATCH_UNMATCHED, authority=self.authority,
                note=(f"别名表把「{raw}」映射到「{target}」，但页面选项尚未探针"
                      f"产出（{self._options_filename()} 为空）——无法确认该值"
                      f"能选中；请先运行 tools/probe_options.py 抓取选项"
                      f"（不猜，也不放行）"))
        matched = _unique(self._option_index.get(normalize(target), []))
        if not matched:
            return MatchResult(
                raw=raw, status=MATCH_ALIAS_OUT_OF_OPTIONS, candidates=[target],
                authority=self.authority,
                note=(f"别名表把「{raw}」映射到「{target}」，但该值不在页面选项"
                      f"集合内——别名表需人工核对（不猜，绝不填相近值）"))
        if len(matched) > 1:
            return MatchResult(
                raw=raw, status=MATCH_AMBIGUOUS, candidates=matched,
                authority=self.authority,
                note=(f"别名表目标「{target}」在页面选项里对应多个选项："
                      f"{'、'.join(matched)}——歧义，不猜"))
        return MatchResult(raw=raw, canonical=matched[0], status=MATCH_ALIAS,
                           candidates=matched, authority=self.authority,
                           note=f"经别名表：「{raw}」→「{matched[0]}」")

    def _unmatched(self, raw: str) -> MatchResult:
        if not self.options:
            note = (f"页面下拉选项尚未探针产出（{self._options_filename()} 为空）"
                    f"——「{raw}」无法确认能否选中；请先运行 "
                    f"tools/probe_options.py 抓取选项")
        else:
            note = (f"「{raw}」不在页面选项/别名表中"
                    f"（需人工确认后补别名，不猜）")
        return MatchResult(raw=raw, status=MATCH_UNMATCHED,
                           authority=self.authority, note=note)

    def _options_filename(self) -> str:
        return os.path.basename(self.options_path) or "options_*.yaml"

    def _aliases_filename(self) -> str:
        return os.path.basename(self.aliases_path) or "*_aliases.yaml"

    def contains(self, canonical: str) -> bool:
        """权威值校验：结果必须落在权威集合内（硬规则）。"""
        return normalize(canonical) in {
            normalize(v) for v in self.authority_values}


class DictionarySet:
    """一组字典（school / grade / class_name）。"""

    def __init__(self, dictionaries: Optional[Dict[str, Dictionary]] = None):
        self._dicts: Dict[str, Dictionary] = dictionaries or {}

    def get(self, key: str) -> Optional[Dictionary]:
        return self._dicts.get(key)

    def match(self, key: str, text) -> MatchResult:
        """按字典名匹配；字典不存在时返回原值（视为无需归一化）。"""
        dictionary = self._dicts.get(key)
        if dictionary is None:
            return MatchResult(raw="" if text is None else str(text),
                               canonical="" if text is None else str(text),
                               status=MATCH_EXACT, authority="none",
                               note="无对应字典，按原值使用")
        return dictionary.match(text)

    @property
    def options_ready(self) -> bool:
        return all(d.options_ready for d in self._dicts.values())

    def missing_options(self) -> List[str]:
        """尚未探针产出选项的**下拉**字典名列表（文本输入字段无需选项）。"""
        return [name for name, d in self._dicts.items()
                if not d.options_ready and not d.is_text_field]

    def unmatched(self, key: str, values: Iterable[str]) -> List[str]:
        """批量匹配，返回未命中/歧义的原始值（用于导入报告）。"""
        bad: List[str] = []
        for value in values:
            if not self.match(key, value).ok:
                bad.append(str(value))
        return _unique(bad)


def load_dictionary_set(config_dir: str = "",
                        strip_noise_enabled: bool = False,
                        kinds: Optional[Dict[str, str]] = None) -> DictionarySet:
    """载入三份字典（school / grade / class_name）。

    文件命名与设计文档附录 C.4 一致：
      config/options_school.yaml + config/school_aliases.yaml
      config/options_grade.yaml  + config/grade_aliases.yaml
      config/options_class.yaml  + config/class_aliases.yaml

    :param kinds: 字段 key → 控件类型（``choice`` 下拉 / ``text`` 文本输入），
        来自 ``config/batch.yaml`` 的 ``dictionary.kinds``（实测得出：
        学校名称在投保页上是文本输入，没有选项集合）
    """
    base = config_dir or _DEFAULT_CONFIG_DIR
    kinds = dict(DEFAULT_KINDS, **(kinds or {}))
    specs = {
        "school": ("options_school.yaml", "school_aliases.yaml"),
        "grade": ("options_grade.yaml", "grade_aliases.yaml"),
        "class_name": ("options_class.yaml", "class_aliases.yaml"),
    }
    dictionaries: Dict[str, Dictionary] = {}
    for key, (options_file, aliases_file) in specs.items():
        dictionaries[key] = Dictionary.load(
            name=key,
            options_path=os.path.join(base, options_file),
            aliases_path=os.path.join(base, aliases_file),
            strip_noise_enabled=strip_noise_enabled,
            kind=kinds.get(key, KIND_CHOICE))
    return DictionarySet(dictionaries)


def _unique(values: Iterable[str]) -> List[str]:
    """去重且保持顺序。"""
    seen: List[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return seen


def _load_yaml_list(path: str) -> List[str]:
    """读 ``options:`` 列表。"""
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return []
    return [str(o) for o in (data.get("options") or [])]


def _load_yaml_map(path: str) -> Dict[str, str]:
    """读 ``aliases:`` 映射。"""
    data = _load_yaml(path)
    if not isinstance(data, dict):
        return {}
    aliases = data.get("aliases") or {}
    if not isinstance(aliases, dict):
        return {}
    return {str(k): str(v) for k, v in aliases.items()}


def _load_yaml(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 — 配置损坏不应导致导入崩溃
        return {}
