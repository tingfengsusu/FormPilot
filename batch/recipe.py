"""业务配方（Recipe）—— 把「一条业务」抽象成一份 YAML（设计文档第八节）。

    config/recipes/学平险.yaml
      name / input / validate / execute / defaults / review / states

**新增一个业务 = 加一份 YAML + 可能加一个字段映射器，不动主流程。**
同一套 validate / review / states 可以承载后面的业务（新客户单条录入、合同生成）。

同时负责读取 ``config/batch.yaml``（模块级默认配置），Recipe 的对应段落
**覆盖**它，从而做到「配置文件里改行为，不改代码」。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

import yaml

from .dictionaries import DictionarySet, load_dictionary_set
from .validator import (DEFAULT_AGE_RANGE, DEFAULT_PERIOD_DAYS,
                       DEFAULT_PHONE_REGEX, DEFAULT_REF_YEAR, Validator)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_RECIPE_DIR = os.path.join(_PROJECT_ROOT, "config", "recipes")
_DEFAULT_BATCH_CONFIG = os.path.join(_PROJECT_ROOT, "config", "batch.yaml")

#: 执行段默认值。
DEFAULT_EXECUTE: Dict[str, Any] = {
    "product": "",
    "property_map": {},
    "default_applicant_name": "current_operator",
    "current_operator": "",
    "max_retries": 3,
    "retry_backoff_seconds": 3,
    "interval_seconds": 2,
    "screenshot": True,
    "headless": False,
    #: 人工确认时机：batch（批末统一复核）| per_record（逐条确认 —— 填错删不掉的场景用）
    "confirm_policy": "batch",
    #: 归档对比报告（HTML：期望/实际/状态 + 原始截图链接）
    "report_html": True,
}

#: 复核段默认值。
DEFAULT_REVIEW: Dict[str, Any] = {
    "mode": "exception_first",
    "bulk_pass": True,
    "target_seconds_per_record": 5,
}

#: 状态段默认值（与 batch.models 一致）。
DEFAULT_STATES: Dict[str, Any] = {
    "initial": "未确认",
    "flow": ["未确认", "已确认", "录入中", "待复核", "已复核", "已支付"],
    "terminal": ["已支付"],
    "error": ["录入失败", "数据异常"],
}


def _load_yaml(path: str) -> dict:
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并（override 优先），用于「Recipe 覆盖 batch.yaml」。"""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class ValidateSpec:
    """校验规则清单的结构化形态（由 Recipe 的 validate 列表解析而来）。"""

    required: List[str] = field(default_factory=list)
    age_range: Sequence[int] = DEFAULT_AGE_RANGE
    period_days: Sequence[int] = DEFAULT_PERIOD_DAYS
    phone_regex: str = DEFAULT_PHONE_REGEX
    ref_year: int = DEFAULT_REF_YEAR
    duplicate_check: bool = True
    dictionary: bool = True
    dictionary_fields: Dict[str, str] = field(default_factory=dict)
    rules: List[str] = field(default_factory=list)

    def to_validator(self, dictionaries: Optional[DictionarySet] = None,
                     student_level_required: bool = False) -> Validator:
        return Validator(
            required=self.required or list(DEFAULT_REQUIRED_FALLBACK),
            age_range=self.age_range,
            period_days=self.period_days,
            phone_regex=self.phone_regex,
            ref_year=self.ref_year,
            dictionaries=dictionaries,
            dictionary_fields=self.dictionary_fields or None,
            duplicate_check=self.duplicate_check,
            student_level_required=student_level_required,
        )


#: validate 段未声明 required 时的兜底。
DEFAULT_REQUIRED_FALLBACK = ("name", "id_card", "phone", "school",
                             "class_name", "period_start", "period_end")


@dataclass
class Recipe:
    """一份业务配方。"""

    name: str = ""
    description: str = ""
    path: str = ""
    input_config: Dict[str, Any] = field(default_factory=dict)
    validate_spec: ValidateSpec = field(default_factory=ValidateSpec)
    execute: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_EXECUTE))
    defaults: Dict[str, Any] = field(default_factory=dict)
    review: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_REVIEW))
    states: Dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_STATES))
    settings: Dict[str, Any] = field(default_factory=dict)
    #: 学生类别推导规则（config/batch.yaml 的 student_level 段）
    student_level_config: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # 载入
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, source: Optional[str] = None,
             settings_path: str = _DEFAULT_BATCH_CONFIG) -> "Recipe":
        """载入配方。

        :param source: 配方名（``学平险``）、文件名或完整路径；None 表示
            使用 config/recipes/ 下的第一个配方（按文件名排序）
        """
        path = _resolve_recipe_path(source)
        data = _load_yaml(path)
        settings = _load_yaml(settings_path)

        recipe = cls(
            name=data.get("name") or os.path.splitext(os.path.basename(path))[0],
            description=data.get("description", ""),
            path=path,
            input_config=_deep_merge(settings.get("import", {}),
                                     data.get("input", {})),
            validate_spec=_parse_validate(
                data.get("validate"),
                settings.get("validate", {}),
                ref_year=(data.get("input") or {}).get(
                    "ref_year", settings.get("import", {}).get("ref_year", DEFAULT_REF_YEAR))),
            execute=_deep_merge(DEFAULT_EXECUTE,
                                _deep_merge(settings.get("execute", {}),
                                            data.get("execute", {}))),
            defaults=data.get("defaults", {}) or {},
            review=_deep_merge(DEFAULT_REVIEW, data.get("review", {})),
            states=_deep_merge(DEFAULT_STATES, data.get("states", {})),
            settings=settings,
            student_level_config=dict(settings.get("student_level") or {}),
        )
        # batch.yaml 的 execute.product 也作为兜底（Recipe 未声明时）
        if not recipe.execute.get("product"):
            recipe.execute["product"] = settings.get("execute", {}).get("product", "")
        return recipe

    @staticmethod
    def list_recipes(recipe_dir: str = _DEFAULT_RECIPE_DIR) -> List[str]:
        """列出可用配方名（不含扩展名）。"""
        if not os.path.isdir(recipe_dir):
            return []
        return sorted(os.path.splitext(f)[0] for f in os.listdir(recipe_dir)
                      if f.endswith(".yaml") and not f.startswith("_"))

    # ------------------------------------------------------------------
    # 派生
    # ------------------------------------------------------------------
    def build_dictionaries(self, config_dir: str = "") -> DictionarySet:
        """按配方声明的字典名载入字典。"""
        base = config_dir or _PROJECT_ROOT
        dictionary_cfg = self.settings.get("dictionary", {}) or {}
        return load_dictionary_set(
            config_dir=os.path.join(base, dictionary_cfg.get("config_dir", "config")),
            strip_noise_enabled=bool(dictionary_cfg.get("strip_noise", False)),
            kinds={str(k): str(v) for k, v in
                   (dictionary_cfg.get("kinds") or {}).items()})

    def build_validator(self, dictionaries: Optional[DictionarySet] = None) -> Validator:
        return self.validate_spec.to_validator(
            dictionaries, student_level_required=self.student_level_required)

    # ------------------------------------------------------------------
    @property
    def student_level_rules(self) -> List[dict]:
        """学生类别推导规则（原始年级写法 → 学生类别）。"""
        return [dict(rule) for rule in
                (self.student_level_config.get("rules") or [])
                if isinstance(rule, dict)]

    @property
    def student_level_required(self) -> bool:
        """学生类别是否必须推导出来（推导不出来 → 数据异常，不自动录入）。"""
        return bool(self.student_level_config.get("require", True))

    @property
    def mapping_path(self) -> str:
        """列名映射文件路径。"""
        value = self.input_config.get("mapping") or "config/import_mapping.yaml"
        return value if os.path.isabs(value) else os.path.join(_PROJECT_ROOT, value)

    @property
    def input_type(self) -> str:
        return str(self.input_config.get("type", "excel")).lower()

    @property
    def ref_year(self) -> int:
        return int(self.input_config.get("ref_year", DEFAULT_REF_YEAR))

    @property
    def product_name(self) -> str:
        return str(self.execute.get("product", "") or "")

    @property
    def property_map(self) -> Dict[str, str]:
        """品种属性名 → 明细字段名。"""
        return {str(k): str(v) for k, v in
                (self.execute.get("property_map") or {}).items()}

    @property
    def max_retries(self) -> int:
        return int(self.execute.get("max_retries", 3))

    @property
    def retry_backoff_seconds(self) -> float:
        return float(self.execute.get("retry_backoff_seconds", 3))

    @property
    def interval_seconds(self) -> float:
        return float(self.execute.get("interval_seconds", 0))

    @property
    def screenshot_enabled(self) -> bool:
        return bool(self.execute.get("screenshot", True))

    def storage_path(self, key: str, default: str) -> str:
        """读取 settings.storage.<key>，相对路径按项目根解析。"""
        value = (self.settings.get("storage", {}) or {}).get(key, default)
        return value if os.path.isabs(value) else os.path.join(_PROJECT_ROOT, value)

    def required_in_multi_user(self, fieldname: str) -> bool:
        node = (self.defaults or {}).get(fieldname) or {}
        return bool(node.get("required_in_multi_user", False))

    def applicant_name_mode(self) -> str:
        node = (self.defaults or {}).get("applicant_name") or {}
        return str(node.get("mode", "current_operator"))


def _resolve_recipe_path(source: Optional[str]) -> str:
    """把配方名/文件名/路径解析为实际文件路径。"""
    if source and os.path.isabs(source) and os.path.exists(source):
        return source
    if source:
        for candidate in (
            os.path.join(_DEFAULT_RECIPE_DIR, f"{source}.yaml"),
            os.path.join(_DEFAULT_RECIPE_DIR, source),
            os.path.join(_PROJECT_ROOT, source),
        ):
            if os.path.exists(candidate):
                return candidate
        raise FileNotFoundError(f"未找到配方：{source}（查找目录 {_DEFAULT_RECIPE_DIR}）")

    names = Recipe.list_recipes()
    if not names:
        raise FileNotFoundError(f"配方目录为空：{_DEFAULT_RECIPE_DIR}")
    return os.path.join(_DEFAULT_RECIPE_DIR, f"{names[0]}.yaml")


def _parse_validate(validate_list: Any, settings: dict,
                    ref_year: Any) -> ValidateSpec:
    """把 validate 规则清单解析为 :class:`ValidateSpec`。

    规则清单形如::

        - required: [name, id_card, ...]
        - id_card_checksum
        - age_range: [5, 20]
        - period_sanity
        - duplicate_check

    未声明的项取 ``config/batch.yaml`` 的 ``validate`` 段，再取内置默认值。
    """
    spec = ValidateSpec(
        required=list(settings.get("required") or []),
        age_range=settings.get("age_range") or DEFAULT_AGE_RANGE,
        period_days=settings.get("period_days") or DEFAULT_PERIOD_DAYS,
        phone_regex=settings.get("phone_regex") or DEFAULT_PHONE_REGEX,
        duplicate_check=bool(settings.get("duplicate_check", True)),
        ref_year=int(ref_year if ref_year is not None else DEFAULT_REF_YEAR),
    )

    if not isinstance(validate_list, list):
        return spec

    for entry in validate_list:
        if isinstance(entry, str):
            key, value = entry, None
        elif isinstance(entry, dict) and entry:
            key, value = next(iter(entry.items()))
        else:
            continue
        spec.rules.append(str(key))
        if key == "required" and isinstance(value, list):
            spec.required = [str(v) for v in value]
        elif key == "age_range" and isinstance(value, list) and len(value) == 2:
            spec.age_range = (int(value[0]), int(value[1]))
        elif key == "period_sanity" and isinstance(value, dict):
            if value.get("days"):
                spec.period_days = tuple(value["days"])
        elif key == "period_sanity" and isinstance(value, list) and len(value) == 2:
            spec.period_days = (int(value[0]), int(value[1]))
        elif key == "phone_format":
            if isinstance(value, str):
                spec.phone_regex = value
        elif key == "duplicate_check":
            spec.duplicate_check = bool(value) if value is not None else True
        elif key == "dictionary":
            spec.dictionary = bool(value) if value is not None else True
    return spec


def load_batch_settings(path: str = _DEFAULT_BATCH_CONFIG) -> dict:
    """读取 config/batch.yaml（模块级默认配置）。"""
    return _load_yaml(path)
