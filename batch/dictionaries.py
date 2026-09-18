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

**硬规则**：归一化后的匹配结果必须落在页面选项集合内，否则一律进「数据异常」，
绝不猜一个相近值填进去——下拉里填了错误的值，可能产生一份错误的保单。

选项字典尚未探针产出时（options 为空），回退以别名表的目标值为权威
（``authority="alias"``），并在匹配结果里标注 ``options_not_ready``，
便于界面提示「先跑 P-1 探针」；探针产出后配置项
``dictionary.require_probed_options: true`` 即启用严格模式。
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

#: 归一化时剥离的常见噪声后缀/前缀（仅作可选，默认关闭）。
_NOISE_SUFFIXES = ("学校", "小学", "中学", "高中", "幼儿园", "校区")
_NOISE_PREFIXES = ("洛阳市", "洛阳市区", "洛阳")

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "（）【】《》，。、：；！？　",
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
    authority: str = ""                 # "options"（页面选项）| "alias"（别名表目标值）
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in (MATCH_EXACT, MATCH_ALIAS)

    @property
    def detail(self) -> str:
        if self.ok:
            return self.note or f"匹配到「{self.canonical}」（{self.authority}）"
        if self.status == MATCH_EMPTY:
            return "值为空"
        if self.status == MATCH_AMBIGUOUS:
            return (f"「{self.raw}」同时命中多个候选：{'、'.join(self.candidates)}"
                    f"——歧义，不猜")
        return f"「{self.raw}」不在页面选项/别名表中（需人工确认后补别名）"


class Dictionary:
    """一份字典：权威选项（options）+ 别名表（aliases）。"""

    def __init__(self, name: str,
                 options: Optional[Iterable[str]] = None,
                 aliases: Optional[Dict[str, str]] = None,
                 options_path: str = "",
                 aliases_path: str = "",
                 strip_noise_enabled: bool = False):
        self.name = name
        self.options: List[str] = [str(o) for o in (options or []) if str(o).strip()]
        self.aliases: Dict[str, str] = {
            normalize(k): str(v) for k, v in (aliases or {}).items() if str(k).strip()
        }
        self.options_path = options_path
        self.aliases_path = aliases_path
        self.strip_noise_enabled = strip_noise_enabled

        #: 选项归一化索引（去重：同归一化值对应多个选项即歧义）
        self._option_index: Dict[str, List[str]] = {}
        for option in self.options:
            self._option_index.setdefault(normalize(option), []).append(option)

        #: 别名目标值的归一化索引（用于「以别名为权威」的过渡模式）
        self._alias_target_index: Dict[str, List[str]] = {}
        for target in self.aliases.values():
            self._alias_target_index.setdefault(normalize(target), []).append(target)

    # ------------------------------------------------------------------
    # 载入
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, name: str, options_path: str, aliases_path: str,
             strip_noise_enabled: bool = False) -> "Dictionary":
        """从 options / aliases 两个 yaml 载入（文件缺失按空处理）。"""
        options = _load_yaml_list(options_path)
        aliases = _load_yaml_map(aliases_path)
        return cls(name=name, options=options, aliases=aliases,
                   options_path=options_path, aliases_path=aliases_path,
                   strip_noise_enabled=strip_noise_enabled)

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------
    @property
    def options_ready(self) -> bool:
        """页面选项是否已探针产出。"""
        return bool(self.options)

    @property
    def authority_values(self) -> List[str]:
        """权威值集合：优先页面选项，否则退化为别名表的目标值。"""
        if self.options:
            return list(self.options)
        seen: List[str] = []
        for target in self.aliases.values():
            if target not in seen:
                seen.append(target)
        return seen

    @property
    def authority(self) -> str:
        return "options" if self.options else "alias"

    def match(self, text) -> MatchResult:
        """匹配一个台账写法到权威值。

        命中唯一 → ok；命中多个 → 歧义；未命中 → unmatched。
        """
        raw = "" if text is None else str(text)
        key = normalize(raw)
        if not key:
            return MatchResult(raw=raw, status=MATCH_EMPTY, authority=self.authority,
                               note="值为空")

        candidates: List[str] = []

        # 1) 选项文本直接命中
        candidates.extend(self._option_index.get(key, []))

        # 2) 别名表命中（别名 → 权威值）
        alias_target = self.aliases.get(key)
        if alias_target:
            candidates.append(alias_target)
        else:
            # 2b) 选项未产出时，允许「别名目标值」作为权威直接命中
            if not self.options:
                candidates.extend(self._alias_target_index.get(key, []))

        # 3) 去噪声后再试一轮（可选）
        if not candidates and self.strip_noise_enabled:
            stripped = strip_noise(raw)
            candidates.extend(self._option_index.get(stripped, []))
            target = self.aliases.get(stripped)
            if target:
                candidates.append(target)
            if not self.options:
                candidates.extend(self._alias_target_index.get(stripped, []))

        unique = _unique(candidates)
        if len(unique) == 1:
            canonical = unique[0]
            status = MATCH_EXACT if canonical in self._option_index.get(key, []) \
                else MATCH_ALIAS
            note = ""
            if not self.options_ready:
                note = (f"匹配到「{canonical}」（页面选项尚未探针产出，"
                        f"当前以别名表为权威，请先运行 tools/probe_options.py）")
            return MatchResult(raw=raw, canonical=canonical, status=status,
                               candidates=unique, authority=self.authority, note=note)
        if len(unique) > 1:
            return MatchResult(raw=raw, status=MATCH_AMBIGUOUS, candidates=unique,
                               authority=self.authority)
        return MatchResult(raw=raw, status=MATCH_UNMATCHED, authority=self.authority)

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
        """尚未探针产出选项的字典名列表。"""
        return [name for name, d in self._dicts.items() if not d.options_ready]

    def unmatched(self, key: str, values: Iterable[str]) -> List[str]:
        """批量匹配，返回未命中/歧义的原始值（用于导入报告）。"""
        bad: List[str] = []
        for value in values:
            if not self.match(key, value).ok:
                bad.append(str(value))
        return _unique(bad)


def load_dictionary_set(config_dir: str = "",
                        strip_noise_enabled: bool = False) -> DictionarySet:
    """载入三份字典（school / grade / class_name）。

    文件命名与设计文档附录 C.4 一致：
      config/options_school.yaml + config/school_aliases.yaml
      config/options_grade.yaml  + config/grade_aliases.yaml
      config/options_class.yaml  + config/class_aliases.yaml
    """
    base = config_dir or _DEFAULT_CONFIG_DIR
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
            strip_noise_enabled=strip_noise_enabled)
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
