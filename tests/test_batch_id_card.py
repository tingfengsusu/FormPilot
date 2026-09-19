"""batch/id_card.py 测试 —— 校验位算法（整个方案的地基）。

覆盖：
  - 算法常量与设计文档 6.1 一致（防止手滑改动）
  - 校验位：正确通过 / 任意一位改动都不通过 / 长度与格式不符不通过
  - 归一化：全角数字、空白、小写 x
  - 出生日期、性别、地区码、周岁年龄
  - validate_structure 的结论与错误说明

测试样本全部由 _helpers.make_id_card 现场生成（虚构），不含真实身份证号。
"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card

from batch import id_card as ic


def test_algorithm_constants():
    """加权因子与校验码表必须与 GB 11643-1999 一致。"""
    assert ic.WEIGHTS == [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    assert ic.CHECK_MAP == "10X98765432"
    assert ic.ID_LENGTH == 18


def test_checksum_accepts_generated_ids():
    """生成的虚构号码（含 X 校验位）必须通过。"""
    samples = [
        make_id_card("410305", "20150901", "12", "男"),
        make_id_card("410305", "20120315", "34", "女"),
        make_id_card("110101", "20080101", "01", "男"),
    ]
    for sample in samples:
        assert len(sample) == 18
        assert ic.check_checksum(sample), sample[:6]  # 只报前 6 位，避免打印样本全号


def test_checksum_rejects_single_digit_change():
    """任何一位被改动（错一位/串位）都必须被判错。"""
    good = make_id_card("410305", "20150901", "12", "男")
    for i in range(17):
        digit = int(good[i])
        changed = good[:i] + str((digit + 1) % 10) + good[i + 1:]
        assert not ic.check_checksum(changed), f"第 {i + 1} 位改动后仍通过"
    # 校验位本身改错
    last = "0" if good[17] != "0" else "1"
    assert not ic.check_checksum(good[:17] + last)


def test_checksum_rejects_bad_shape():
    assert not ic.check_checksum("")
    assert not ic.check_checksum(None)
    assert not ic.check_checksum("41030520150901123")        # 17 位
    assert not ic.check_checksum("4103052015090112345")      # 19 位
    assert not ic.check_checksum("41030520150901123A")       # 前 17 位含字母
    assert ic.check_format("41030520150901123X") is True
    assert ic.check_format("41030520150901123x") is True


def test_normalize_fullwidth_and_spaces():
    raw = " ４１０３05 2015 0901 123X\u3000"
    assert ic.normalize(raw) == "41030520150901123X"
    assert ic.normalize(None) == ""


def test_birthdate_gender_province():
    sample = make_id_card("410305", "20150901", "12", "男")
    assert ic.parse_birthdate(sample) == date(2015, 9, 1)
    assert ic.gender_of(sample) == "男"
    assert ic.province_of(sample) == "河南"

    female = make_id_card("110101", "20080229", "34", "女")
    assert ic.parse_birthdate(female) == date(2008, 2, 29)   # 闰年 2 月 29 日
    assert ic.gender_of(female) == "女"
    assert ic.province_of(female) == "北京"

    # 出生日期非法（2 月 30 日）→ None，且结构校验给出原因
    bad = make_id_card("410305", "20150230", "12")
    assert ic.parse_birthdate(bad) is None


def test_age_at():
    birth = date(2015, 9, 1)
    assert ic.age_at(birth, date(2026, 8, 22)) == 10   # 生日未到
    assert ic.age_at(birth, date(2026, 9, 1)) == 11    # 生日当天
    assert ic.age_at(birth, date(2026, 9, 2)) == 11


def test_validate_structure_ok_and_errors():
    good = ic.validate_structure(make_id_card("410305", "20150901", "12", "男"))
    assert good.ok and good.format_ok and good.checksum_ok
    assert good.birthdate == date(2015, 9, 1)
    assert good.gender == "男" and good.province == "河南"

    empty = ic.validate_structure("")
    assert not empty.ok and "为空" in empty.detail

    wrong = make_id_card("410305", "20150901", "12")
    wrong = wrong[:17] + ("0" if wrong[17] != "0" else "1")
    bad = ic.validate_structure(wrong)
    assert not bad.ok
    assert "校验位不通过" in bad.detail

    illegal_date = make_id_card("410305", "20150230", "12")
    check = ic.validate_structure(illegal_date)
    assert not check.ok and "出生日期非法" in check.detail


def test_validate_structure_unknown_province():
    """前 2 位不在省级码表里 → 报地区码无效。"""
    sample = "99030520150901123" + ic.CHECK_MAP[
        sum(int("99030520150901123"[i]) * ic.WEIGHTS[i] for i in range(17)) % 11]
    check = ic.validate_structure(sample)
    assert check.province == "" and "地区码无效" in check.detail


if __name__ == "__main__":
    test_algorithm_constants()
    test_checksum_accepts_generated_ids()
    test_checksum_rejects_single_digit_change()
    test_checksum_rejects_bad_shape()
    test_normalize_fullwidth_and_spaces()
    test_birthdate_gender_province()
    test_age_at()
    test_validate_structure_ok_and_errors()
    test_validate_structure_unknown_province()
    print("test_batch_id_card.py 全部通过 ✓")
