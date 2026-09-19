"""batch/validator.py 测试 —— 必填 / 身份证 / 手机号 / 保险期间 / 重复 / 字典。

测试样本全部虚构（身份证号由 _helpers.make_id_card 现场生成）。
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card

from batch.dictionaries import Dictionary, DictionarySet
from batch.models import Applicant
from batch.validator import (DEFAULT_AGE_RANGE, DEFAULT_PERIOD_DAYS,
                             RULE_AGE_RANGE, RULE_CHECKSUM, RULE_DICTIONARY,
                             RULE_DUPLICATE, RULE_FORMAT, RULE_PERIOD_SANITY,
                             RULE_REQUIRED, Validator, derive_student_level,
                             normalize_phone, parse_date, resolve_period)

GOOD_ID = make_id_card("410305", "20150901", "12")          # 2015-09-01 生
ADULT_ID = make_id_card("410305", "19900101", "34")


def _validator(**kwargs) -> Validator:
    defaults = dict(
        required=["name", "id_card", "phone", "school", "class_name",
                  "period_start", "period_end"],
        age_range=(5, 20),
        period_days=(360, 370),
        dictionaries=DictionarySet({}),
    )
    defaults.update(kwargs)
    return Validator(**defaults)


def _applicant(**kwargs) -> Applicant:
    values = dict(
        id=1, batch_id=1, name="测试学生", id_card=GOOD_ID,
        phone="13800000000", school="柳集小学", class_name="1班",
        period_start="2026-08-22", period_end="2027-08-21",
    )
    values.update(kwargs)
    return Applicant(**values)


def test_required():
    report = _validator().validate(_applicant(name="", phone=""))
    failed = {r.field for r in report.failed}
    assert failed == {"name", "phone"}
    assert all(r.rule == RULE_REQUIRED for r in report.failed)
    assert not report.passed


def test_checksum_and_birthdate():
    report = _validator().validate(_applicant())
    rules = {r.rule: r for r in report.results}
    assert rules[RULE_CHECKSUM].passed
    assert rules[RULE_AGE_RANGE].passed           # 2026-08-22 时 10 岁

    bad = GOOD_ID[:17] + ("0" if GOOD_ID[17] != "0" else "1")
    report2 = _validator().validate(_applicant(id_card=bad))
    failed_rules = {r.rule for r in report2.failed}
    assert RULE_CHECKSUM in failed_rules
    assert "校验位不通过" in report2.reason


def test_age_range():
    report = _validator().validate(_applicant(id_card=ADULT_ID))
    assert RULE_AGE_RANGE in {r.rule for r in report.failed}
    # 年龄区间可配置
    assert _validator(age_range=(5, 60)).validate(
        _applicant(id_card=ADULT_ID)).passed


def test_phone_format():
    assert _validator().validate(_applicant()).passed
    assert _validator().validate(
        _applicant(phone="1380000000")).failed[0].rule == RULE_FORMAT
    assert _validator().validate(
        _applicant(phone="+86 138-0000-0000")).passed
    assert normalize_phone("１３８ ００００ ００００") == "13800000000"
    # 空手机号由必填规则负责，格式规则不重复报
    assert not any(r.field == "phone" and r.rule == RULE_FORMAT
                   for r in _validator().validate(_applicant(phone="")).results)


def test_period_sanity():
    assert _validator().validate(_applicant()).passed

    reversed_report = _validator().validate(_applicant(
        period_start="2027-08-21", period_end="2026-08-22"))
    assert RULE_PERIOD_SANITY in {r.rule for r in reversed_report.failed}

    too_long = _validator().validate(_applicant(
        period_start="2026-08-22", period_end="2028-08-21"))
    assert "时长异常" in too_long.reason

    unparsable = _validator().validate(_applicant(
        period_start="2026-13-01", period_end="2027-08-21"))
    assert RULE_PERIOD_SANITY in {r.rule for r in unparsable.failed}

    # 两条都为空 → 由必填规则负责
    assert not any(r.rule == RULE_PERIOD_SANITY for r in
                   _validator().validate(_applicant(
                       period_start="", period_end="")).results)


def test_duplicate():
    report = _validator().validate(_applicant(), duplicate_ids={1})
    assert RULE_DUPLICATE in {r.rule for r in report.failed}
    assert _validator().validate(_applicant(), duplicate_ids={2}).passed
    # 未传重复集合 → 不判重复（调用方负责算）
    assert _validator().validate(_applicant()).passed


def test_dictionary_strict_match():
    dictionaries = DictionarySet({
        "school": Dictionary("school", options=["柳集小学"],
                             aliases={"柳小": "柳集小学"}),
        "class_name": Dictionary("class_name", options=["1班"]),
    })
    validator = _validator(dictionaries=dictionaries)
    assert validator.validate(_applicant()).passed

    report = validator.validate(_applicant(school="清花园"))
    dictionary_failures = [r for r in report.failed if r.rule == RULE_DICTIONARY]
    assert len(dictionary_failures) == 1
    assert dictionary_failures[0].field == "school"
    assert "清花园" in dictionary_failures[0].detail

    # 空值交给必填规则，字典规则不重复报
    assert not any(r.rule == RULE_DICTIONARY for r in
                   validator.validate(_applicant(school="", class_name="")).results)


def test_derive_student_level():
    """学生类别由**原始**年级写法推导（归一化后的「1年级」分不出小一还是大一）。"""
    rules = [
        {"level": "大中专学校学生", "prefixes": ["大", "中专", "大专", "本科"]},
        {"level": "中学生", "prefixes": ["高", "初", "中", "七", "八", "九"]},
        {"level": "小学生", "prefixes": ["一", "二", "三", "四", "五", "六", "小",
                                         "1", "2", "3", "4", "5", "6"]},
    ]
    # 大学生走「大中专学校学生」（使用者确认：再选 1/2/3）
    assert derive_student_level("大一", rules) == "大中专学校学生"
    assert derive_student_level("大三", rules) == "大中专学校学生"
    assert derive_student_level("中专一年级", rules) == "大中专学校学生"
    assert derive_student_level("一", rules) == "小学生"
    assert derive_student_level("六", rules) == "小学生"
    assert derive_student_level("3", rules) == "小学生"
    assert derive_student_level("高一", rules) == "中学生"
    assert derive_student_level("八", rules) == "中学生"
    # 推导不出来就空着（上层按「数据异常」处理，不猜）
    assert derive_student_level("十一", rules) == ""
    assert derive_student_level("", rules) == ""
    assert derive_student_level(None, rules) == ""


def test_student_level_required_validation():
    """学生类别推导不出来 → 数据异常（否则页面会保留默认类别）。"""
    validator = _validator(student_level_required=True)
    ok = validator.validate(_applicant(grade="一", student_level="小学生"))
    assert ok.passed
    assert any(r.rule == "student_level" and r.passed for r in ok.results)

    bad = validator.validate(_applicant(grade="十一"))     # 没推导出类别
    assert "student_level" in {r.rule for r in bad.failed}
    assert "未能从年级" in bad.reason

    # 未开启该规则时不影响（默认关闭，避免误伤别的业务）
    assert _validator().validate(_applicant(grade="十一")).passed


def test_defaults_documented():
    """默认值与设计文档一致（改动会被这条测试挡住）。"""
    assert DEFAULT_AGE_RANGE == (5, 20)
    assert DEFAULT_PERIOD_DAYS == (360, 370)


def test_parse_date_and_resolve_period():
    assert parse_date("2026-08-22") == date(2026, 8, 22)
    assert parse_date("8月22日").year == 1900      # 无年份哨兵
    period = resolve_period("8.22-8.21", ref_year=2026)
    assert period.ok and period.start == date(2026, 8, 22)
    assert period.end == date(2027, 8, 21)         # 止日期按次年解释
    assert not resolve_period("9.12", ref_year=2026).ok
    assert not resolve_period("9.1-9.0", ref_year=2026).ok


if __name__ == "__main__":
    test_required()
    test_checksum_and_birthdate()
    test_age_range()
    test_phone_format()
    test_period_sanity()
    test_duplicate()
    test_dictionary_strict_match()
    test_derive_student_level()
    test_student_level_required_validation()
    test_defaults_documented()
    test_parse_date_and_resolve_period()
    print("test_batch_validator.py 全部通过 ✓")
