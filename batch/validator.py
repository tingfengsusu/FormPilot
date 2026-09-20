"""校验规则（设计文档第六节）—— 本方案唯一的自动化质量闸门。

**定位（附录 B.1 变更 1）**：校验的对象不是「人工录入的准确性」，而是
「**机器识别的可靠性**」（上游 Excel 是 OCR 产物，必然有错）。规则不变：

    check_required      必填
    check_id_card       身份证校验位 + 出生日期 + 年龄区间 + 地区码
    check_phone         手机号格式
    check_period        保险期间：起 < 止，且时长在合理区间
    check_duplicate     同批次/同期间重复投保
    check_dictionary    学校/年级/班级必须落在页面选项（下拉权威值）内

**校验结果不是「拦下来」，而是决定人工要看多少条**：
全部通过 → 进入录入（复核可批量通过）；有失败 → 进「数据异常」，
只有这些需要人工逐条改。这就是提效的来源。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import id_card as id_card_mod
from .dictionaries import MATCH_EMPTY, DictionarySet, load_dictionary_set
from .models import Applicant, ValidationResult

# ----------------------------------------------------------------------
# 规则名（写入 validation.rule）
# ----------------------------------------------------------------------
RULE_REQUIRED = "required"
RULE_CHECKSUM = "checksum"
RULE_BIRTHDATE = "birthdate"
RULE_AGE_RANGE = "age_range"
RULE_PROVINCE = "province"
RULE_FORMAT = "format"
RULE_PERIOD_SANITY = "period_sanity"
RULE_DUPLICATE = "duplicate"
RULE_DICTIONARY = "dictionary"
RULE_STUDENT_LEVEL = "student_level"

#: 默认必填字段。
DEFAULT_REQUIRED = ("name", "id_card", "phone", "school", "class_name",
                    "period_start", "period_end")

#: 默认手机号规则：11 位、1 开头、第二位 3-9。
DEFAULT_PHONE_REGEX = r"^1[3-9]\d{9}$"

#: 默认年龄区间（按保险期间起始日计算）。
DEFAULT_AGE_RANGE = (5, 20)

#: 默认保险期间长度区间（天）——学平险为 1 年期，365±5。
DEFAULT_PERIOD_DAYS = (360, 370)

#: 默认导入年份（源数据缺年份时的解读基准，附录 A.4「止日期=次年」）。
DEFAULT_REF_YEAR = 2026

#: 保险期间列的可用分隔符。
_RANGE_SEPARATORS = ("至", "到", "~", "～", "—", "－", "–", "-", "~")

#: 从文本中提取「日期片段」：年份可选，支持 2026-08-22 / 2026.8.22 / 8月22日 / 8.22
_DATE_TOKEN_RE = re.compile(
    r"(?:(\d{4})\s*[年/\-.]\s*)?(\d{1,2})\s*[月/\-.]\s*(\d{1,2})\s*日?")

#: 全角字符 → 半角（用于期间/手机号等文本归一化）。
_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９（）～－　．",
    "0123456789()~- .",
)


@dataclass
class PeriodResult:
    """保险期间的解析结果。"""

    start: Optional[date] = None
    end: Optional[date] = None
    error: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.start is not None and self.end is not None and not self.error

    @property
    def days(self) -> Optional[int]:
        if self.start is None or self.end is None:
            return None
        return (self.end - self.start).days

    def iso(self) -> tuple:
        return (self.start.isoformat() if self.start else "",
                self.end.isoformat() if self.end else "")


def normalize_text(text: Any) -> str:
    """文本归一化：全角转半角 + 去空白。"""
    if text is None:
        return ""
    value = str(text).translate(_FULLWIDTH)
    return re.sub(r"[\s\u3000]+", "", value).strip()


def parse_date(text: Any) -> Optional[date]:
    """解析单个日期（缺年份时返回月/日，年份记为 0）。"""
    raw = normalize_text(text)
    if not raw:
        return None
    match = _DATE_TOKEN_RE.search(raw)
    if not match:
        return None
    year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
    try:
        return date(int(year) if year else 1900, month, day)
    except ValueError:
        return None


def normalize_phone(text: Any) -> str:
    """手机号归一化：去空格/连字符、去 +86 / 86 前缀。"""
    raw = normalize_text(text)
    raw = re.sub(r"[^\d+]", "", raw)
    for prefix in ("+86", "0086"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
    if raw.startswith("86") and len(raw) == 13:
        raw = raw[2:]
    return raw


def resolve_period(text: Any, ref_year: int = DEFAULT_REF_YEAR,
                   period_days: Sequence[int] = DEFAULT_PERIOD_DAYS
                   ) -> PeriodResult:
    """把「保险期限」文本解析为起止日期（附录 A.4 的日期规则）。

    统一规则：**学平险均为 1 年期，止日期一律按次年解释**。
    源数据常见形态（实测 119 条）：
      - ``2026-08-22 至 2027-08-21`` 完整区间
      - ``8.22-8.21``                无年份 → 起 = ref_year，止 = 次年（若止 < 起）
      - ``9.12``                     缺区间（OCR 截断）→ 异常
      - ``9.1-9.0``                  非法日期（9月0日不存在）→ 异常
      - ``8.27-8.21``                时长 359 天 → 异常（不在 360~370 区间）
    """
    raw = normalize_text(text)
    if not raw:
        return PeriodResult(error="保险期限为空")

    tokens: List[tuple] = []
    for match in _DATE_TOKEN_RE.finditer(raw):
        year, month, day = match.group(1), int(match.group(2)), int(match.group(3))
        tokens.append((int(year) if year else None, month, day))

    if not tokens:
        return PeriodResult(error=f"无法解析保险期限「{text}」")

    if len(tokens) == 1:
        return PeriodResult(error=f"缺区间（只有单个日期「{raw}」，需人工补全）")

    start_y, start_m, start_d = tokens[0]
    end_y, end_m, end_d = tokens[1]

    try:
        start = date(start_y or ref_year, start_m, start_d)
    except ValueError:
        return PeriodResult(error=f"起始日期非法（{raw}）")
    try:
        end = date(end_y or start.year, end_m, end_d)
    except ValueError:
        return PeriodResult(error=f"结束日期非法（{raw}）")

    note = ""
    if end_y is None:
        # 止日期按次年解释：不满一年则跨到次年
        if end <= start:
            try:
                end = date(end.year + 1, end_m, end_d)
            except ValueError:
                return PeriodResult(error=f"结束日期非法（{raw}）")
            note = "止日期按次年解释"
    elif end <= start:
        return PeriodResult(error=f"保险期间起止颠倒（{raw}）")

    result = PeriodResult(start=start, end=end, note=note)
    if period_days:
        low, high = int(period_days[0]), int(period_days[1])
        days = result.days or 0
        if not (low <= days <= high):
            return PeriodResult(
                start=start, end=end, note=note,
                error=f"保险期间时长异常（{days} 天，应在 {low}~{high} 天）")
    return result


def resolve_period_floor(value: Any, today: Optional[date] = None
                         ) -> Optional[date]:
    """解析「保险期间起期最早允许到哪天」的配置。

    实测（2026-09-20 再次确认）：投保页的「保险期间·起」是日期选择器，
    日期列**从明天开始**（今天选不了）——所以下限取 ``tomorrow``。

    - ``today`` / ``今天``：取 today（默认 date.today()）
    - ``tomorrow`` / ``明天``：取 today + 1 天（**页面实测的最早值**）
    - ``YYYY-MM-DD``：指定最早日期
    - 空 / ``off`` / ``none`` / ``false``：不限制（返回 None）
    """
    text = normalize_text(value).lower()
    if not text or text in ("off", "none", "false", "0"):
        return None
    base = today or date.today()
    if text in ("today", "今天"):
        return base
    if text in ("tomorrow", "明天", "+1", "next_day"):
        return base + timedelta(days=1)
    parsed = parse_date(text)
    if parsed is None or parsed.year == 1900:   # 无年份哨兵 = 配置写错
        return None
    return parsed


def shift_period_to_floor(start: date, end: date, floor: date) -> Tuple[date, date]:
    """把保险期间整体平移到 ``floor``：**保持原时长不变**。

    例：2026-08-22 ~ 2027-08-21（364 天）→ 2026-09-19 ~ 2027-09-18（同样 364 天）。
    """
    days = (end - start).days
    return floor, floor + timedelta(days=days)


def apply_period_floor(period_start: str, period_end: str, floor: Optional[date]
                       ) -> Tuple[str, str, str]:
    """起期早于 ``floor`` 时整体平移，返回 ``(起, 止, 调整说明)``。

    实测：投保页的「保险期间·起」只能选**今天及之后**。导入时按导入日取平，
    **录入时（runner）必须按录入当天再取平一次**——否则今天导入、明天录入时
    起期已经过期，页面会拒绝。台账原值不动（以 Excel 为准）。
    """
    if floor is None or not period_start or not period_end:
        return period_start, period_end, ""
    try:
        start = date.fromisoformat(period_start)
        end = date.fromisoformat(period_end)
    except ValueError:
        return period_start, period_end, ""
    if start >= floor:
        return period_start, period_end, ""
    new_start, new_end = shift_period_to_floor(start, end, floor)
    note = (f"起期按页面约束调整：{period_start} → {new_start.isoformat()}"
            f"（止期 {period_end} → {new_end.isoformat()}，"
            f"保持 {(end - start).days} 天）")
    return new_start.isoformat(), new_end.isoformat(), note


def derive_student_level(raw_grade: Any, rules: Sequence[dict]) -> str:
    """由台账里的**原始年级写法**推导页面上要选的「学生类别」。

    实测（使用者确认）：大学生要选「大中专学校学生」再选 1/2/3，
    所以不能只看归一化后的年级（「1年级」既可能是小学一年级，也可能是大一）。

    :param rules: ``[{"level": "小学生", "prefixes": ["一", "二", ...]}, ...]``，
        按顺序做前缀匹配（先匹配到的赢）
    :return: 学生类别文本；推导不出来返回空串
    """
    raw = normalize_text(raw_grade)
    if not raw:
        return ""
    for rule in rules or []:
        level = str(rule.get("level") or "")
        prefixes = [str(p) for p in (rule.get("prefixes") or []) if str(p)]
        if not level:
            continue
        if raw in prefixes or any(raw.startswith(p) for p in prefixes):
            return level
    return ""


@dataclass
class ValidationReport:
    """一条明细的完整校验结论。"""

    results: List[ValidationResult] = field(default_factory=list)

    @property
    def failed(self) -> List[ValidationResult]:
        return [r for r in self.results if not r.passed]

    @property
    def passed(self) -> bool:
        return not self.failed

    @property
    def reason(self) -> str:
        """失败原因摘要（写入 applicant.fail_reason）。"""
        return "；".join(f"[{r.field}] {r.detail}" for r in self.failed)


class Validator:
    """校验器：一条明细 → 一组 ValidationResult。

    配置项来自 Recipe 的 validate 段（见 :mod:`batch.recipe`）或
    ``config/batch.yaml`` 的 ``validate`` 段。
    """

    def __init__(self,
                 required: Sequence[str] = DEFAULT_REQUIRED,
                 age_range: Sequence[int] = DEFAULT_AGE_RANGE,
                 period_days: Sequence[int] = DEFAULT_PERIOD_DAYS,
                 phone_regex: str = DEFAULT_PHONE_REGEX,
                 ref_year: int = DEFAULT_REF_YEAR,
                 dictionaries: Optional[DictionarySet] = None,
                 dictionary_fields: Optional[Dict[str, str]] = None,
                 duplicate_check: bool = True,
                 student_level_required: bool = False):
        self.required = list(required)
        self.age_range = (int(age_range[0]), int(age_range[1]))
        self.period_days = tuple(period_days) if period_days else ()
        self.phone_regex = re.compile(phone_regex)
        self.ref_year = int(ref_year)
        self.dictionaries = dictionaries if dictionaries is not None \
            else load_dictionary_set()
        #: 记录字段 → 字典名（如 school → school）
        self.dictionary_fields = dict(dictionary_fields or {
            "school": "school", "grade": "grade", "class_name": "class_name",
        })
        self.duplicate_check = duplicate_check
        #: 是否要求学生类别必须推导出来（推导不出来就不该自动录入）
        self.student_level_required = student_level_required

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------
    def validate(self, applicant: Applicant,
                 duplicate_ids: Optional[Iterable[int]] = None
                 ) -> ValidationReport:
        """对一条明细执行全部规则。

        :param duplicate_ids: 参与重复投保组的 applicant id 集合（由调用方
            用 :meth:`BatchDB.find_duplicates` 算好后传入）
        """
        results: List[ValidationResult] = []
        results += self.check_required(applicant)
        results += self.check_id_card(applicant)
        results += self.check_phone(applicant)
        results += self.check_period(applicant)
        results += self.check_dictionary(applicant)
        results += self.check_student_level(applicant)
        if self.duplicate_check:
            results += self.check_duplicate(applicant, duplicate_ids)
        return ValidationReport(results)

    # ------------------------------------------------------------------
    # 必填
    # ------------------------------------------------------------------
    def check_required(self, applicant: Applicant) -> List[ValidationResult]:
        results: List[ValidationResult] = []
        for fieldname in self.required:
            value = getattr(applicant, fieldname, "")
            if not str(value or "").strip():
                results.append(ValidationResult(
                    applicant_id=applicant.id or 0, field=fieldname,
                    rule=RULE_REQUIRED, passed=False,
                    detail=f"必填字段为空：{fieldname}"))
        return results

    # ------------------------------------------------------------------
    # 身份证号
    # ------------------------------------------------------------------
    def check_id_card(self, applicant: Applicant,
                      fieldname: str = "id_card",
                      label: str = "被保人身份证") -> List[ValidationResult]:
        """校验位 + 出生日期 + 年龄区间 + 地区码。"""
        raw = getattr(applicant, fieldname, "")
        check = id_card_mod.validate_structure(raw)
        results: List[ValidationResult] = []

        if not str(raw or "").strip():
            # 空值由 check_required 负责（若该字段为必填）
            return results

        results.append(ValidationResult(
            applicant_id=applicant.id or 0, field=fieldname, rule=RULE_CHECKSUM,
            passed=check.checksum_ok,
            detail=f"{label}校验位通过" if check.checksum_ok
            else f"{label}校验位不通过（{check.detail}）"))

        if check.format_ok:
            results.append(ValidationResult(
                applicant_id=applicant.id or 0, field=fieldname, rule=RULE_BIRTHDATE,
                passed=check.birthdate is not None,
                detail=(f"出生日期 {check.birthdate.isoformat()}"
                        if check.birthdate else f"{label}出生日期非法")))

        if check.province is not None:
            results.append(ValidationResult(
                applicant_id=applicant.id or 0, field=fieldname, rule=RULE_PROVINCE,
                passed=True, detail=f"{label}地区码：{check.province}"))

        # 年龄区间：按保险期间起始日算投保时年龄
        birth = check.birthdate
        if birth is not None:
            basis = self._period_start(applicant) or date(self.ref_year, 9, 1)
            age = id_card_mod.age_at(birth, basis)
            low, high = self.age_range
            results.append(ValidationResult(
                applicant_id=applicant.id or 0, field=fieldname, rule=RULE_AGE_RANGE,
                passed=low <= age <= high,
                detail=(f"{label}投保时年龄 {age} 岁（以 {basis.isoformat()} 计）"
                        if low <= age <= high
                        else f"{label}投保时年龄 {age} 岁，超出学生区间 "
                             f"{low}~{high} 岁（基准 {basis.isoformat()}）")))
        return results

    # ------------------------------------------------------------------
    # 手机号
    # ------------------------------------------------------------------
    def check_phone(self, applicant: Applicant,
                    fieldname: str = "phone",
                    label: str = "手机号") -> List[ValidationResult]:
        raw = getattr(applicant, fieldname, "")
        if not str(raw or "").strip():
            return []
        normalized = normalize_phone(raw)
        ok = bool(self.phone_regex.match(normalized))
        return [ValidationResult(
            applicant_id=applicant.id or 0, field=fieldname, rule=RULE_FORMAT,
            passed=ok,
            detail=(f"{label}格式正确（{normalized}）" if ok
                    else f"{label}格式不正确：「{raw}」（应为 11 位、1 开头）"))]

    # ------------------------------------------------------------------
    # 保险期间
    # ------------------------------------------------------------------
    def check_period(self, applicant: Applicant) -> List[ValidationResult]:
        if not str(applicant.period_start or "").strip() and \
                not str(applicant.period_end or "").strip():
            return []
        # 已落库的起止日期是 ISO 格式，直接判合法性 + 时长
        start = _parse_iso(applicant.period_start)
        end = _parse_iso(applicant.period_end)
        if start is None or end is None:
            return [ValidationResult(
                applicant_id=applicant.id or 0, field="period_start",
                rule=RULE_PERIOD_SANITY, passed=False,
                detail=f"保险期间无法解析：{applicant.period_start} ~ "
                       f"{applicant.period_end}")]
        if end <= start:
            return [ValidationResult(
                applicant_id=applicant.id or 0, field="period_start",
                rule=RULE_PERIOD_SANITY, passed=False,
                detail=f"保险期间起止颠倒（{start} ~ {end}）")]
        days = (end - start).days
        if self.period_days:
            low, high = int(self.period_days[0]), int(self.period_days[1])
            if not (low <= days <= high):
                return [ValidationResult(
                    applicant_id=applicant.id or 0, field="period_start",
                    rule=RULE_PERIOD_SANITY, passed=False,
                    detail=f"保险期间时长异常（{days} 天，应在 {low}~{high} 天）")]
        return [ValidationResult(
            applicant_id=applicant.id or 0, field="period_start",
            rule=RULE_PERIOD_SANITY, passed=True,
            detail=f"保险期间 {start} ~ {end}（{days} 天）")]

    def _period_start(self, applicant: Applicant) -> Optional[date]:
        return _parse_iso(applicant.period_start)

    # ------------------------------------------------------------------
    # 重复投保
    # ------------------------------------------------------------------
    def check_duplicate(self, applicant: Applicant,
                        duplicate_ids: Optional[Iterable[int]] = None
                        ) -> List[ValidationResult]:
        if duplicate_ids is None or applicant.id is None:
            return []
        ids = set(duplicate_ids)
        if applicant.id not in ids:
            return []
        return [ValidationResult(
            applicant_id=applicant.id, field="id_card", rule=RULE_DUPLICATE,
            passed=False,
            detail=f"同期重复投保：同一身份证号在 "
                   f"{applicant.period_start} ~ {applicant.period_end} 出现多次"
                   f"（可能是识别错误或重复录入）")]

    # ------------------------------------------------------------------
    # 字典（下拉选项）
    # ------------------------------------------------------------------
    def check_dictionary(self, applicant: Applicant) -> List[ValidationResult]:
        results: List[ValidationResult] = []
        for fieldname, dict_key in self.dictionary_fields.items():
            raw = getattr(applicant, fieldname, "")
            if not str(raw or "").strip():
                continue  # 空值由必填规则负责
            match = self.dictionaries.match(dict_key, raw)
            results.append(ValidationResult(
                applicant_id=applicant.id or 0, field=fieldname,
                rule=RULE_DICTIONARY, passed=match.ok, detail=match.detail))
        return results

    # ------------------------------------------------------------------
    # 学生类别（录入时要先选的字段）
    # ------------------------------------------------------------------
    def check_student_level(self, applicant: Applicant) -> List[ValidationResult]:
        """学生类别必须能推导出来，否则不自动录入。

        页面上的「学生类别」是必填项且有承保口径含义（小学生 / 中学生 /
        大中专学校学生）。推导不出来时若不管它，页面会保留默认值
        （实测默认「大中专学校学生」）—— 那会把小学生当成大中专学生投保。
        """
        if not self.student_level_required:
            return []
        grade = str(getattr(applicant, "grade", "") or "").strip()
        level = str(getattr(applicant, "student_level", "") or "").strip()
        if level:
            return [ValidationResult(
                applicant_id=applicant.id or 0, field="student_level",
                rule=RULE_STUDENT_LEVEL, passed=True,
                detail=f"学生类别：{level}")]
        if not grade:
            return []      # 年级为空由必填/字典规则负责
        return [ValidationResult(
            applicant_id=applicant.id or 0, field="student_level",
            rule=RULE_STUDENT_LEVEL, passed=False,
            detail=(f"未能从年级「{grade}」推导出学生类别（小学生 / 中学生 / "
                    f"大中专学校学生）——请人工确认后再录入，"
                    f"否则页面会保留默认类别"))]


def validate(rec: Applicant, validator: Optional[Validator] = None,
             duplicate_ids: Optional[Iterable[int]] = None) -> ValidationReport:
    """便捷入口：``validate(rec) -> ValidationReport``（设计文档 5.2 的签名）。"""
    return (validator or Validator()).validate(rec, duplicate_ids=duplicate_ids)


def _parse_iso(text: Any) -> Optional[date]:
    """解析 ISO 日期（YYYY-MM-DD）。"""
    raw = str(text or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def age_range_from_config(config: Dict[str, Any]) -> tuple:
    value = config.get("age_range") or DEFAULT_AGE_RANGE
    return (int(value[0]), int(value[1]))


def period_days_from_config(config: Dict[str, Any]) -> tuple:
    value = config.get("period_days") or DEFAULT_PERIOD_DAYS
    return (int(value[0]), int(value[1]))
