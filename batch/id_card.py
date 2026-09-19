"""身份证号校验（GB 11643-1999）—— 整个方案的地基。

**为什么是地基**：它能把「识别/录入准不准」的问题变成「数学上是否自洽」的
问题。18 位里第 18 位是前 17 位按加权模 11 算出来的，**过不了这一位的必然
是错的**——不需要人判断，程序直接标异常。

在此之上再做交叉验证（用身份证自带的编码信息再筛一遍）：

| 规则 | 做法 | 拦得住什么 |
|---|---|---|
| 出生日期合法 | 取 7-14 位解析日期，检查是否真实存在 | 手写 6/8、0/6 混淆 |
| 年龄区间 | 按保险期间算投保时年龄，应在学生区间 | 录错行、串号 |
| 地区码 | 前 6 位行政区划码，省级码必须是有效的 | 整段抄错/识别串位 |
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional

#: 加权因子（前 17 位）。
WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]

#: 模 11 余数 → 校验码。
CHECK_MAP = "10X98765432"

#: 身份证号长度。
ID_LENGTH = 18

#: 省级行政区划代码（GB/T 2260 前两位）。
PROVINCE_CODES = {
    "11": "北京", "12": "天津", "13": "河北", "14": "山西", "15": "内蒙古",
    "21": "辽宁", "22": "吉林", "23": "黑龙江",
    "31": "上海", "32": "江苏", "33": "浙江", "34": "安徽", "35": "福建",
    "36": "江西", "37": "山东",
    "41": "河南", "42": "湖北", "43": "湖南", "44": "广东", "45": "广西",
    "46": "海南",
    "50": "重庆", "51": "四川", "52": "贵州", "53": "云南", "54": "西藏",
    "61": "陕西", "62": "甘肃", "63": "青海", "64": "宁夏", "65": "新疆",
    "71": "台湾", "81": "香港", "82": "澳门", "91": "国外",
}

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９Ｘｘ", "0123456789Xx")


def normalize(id_card) -> str:
    """归一化：去空白（含全角空格）、全角转半角、校验位 x 转大写。"""
    if id_card is None:
        return ""
    text = str(id_card).translate(_FULLWIDTH_DIGITS)
    text = re.sub(r"[\s\u3000]+", "", text)
    return text.upper()


def check_checksum(id_card: str) -> bool:
    """校验位是否正确（设计文档 6.1 的算法）。"""
    text = normalize(id_card)
    if len(text) != ID_LENGTH:
        return False
    if not text[:17].isdigit():
        return False
    s = sum(int(text[i]) * WEIGHTS[i] for i in range(17))
    return CHECK_MAP[s % 11] == text[17]


def check_format(id_card: str) -> bool:
    """格式：18 位、前 17 位数字、末位数字或 X。"""
    text = normalize(id_card)
    return bool(re.fullmatch(r"\d{17}[\dX]", text))


def parse_birthdate(id_card: str) -> Optional[date]:
    """解析出生日期（第 7-14 位）；不存在或不合法返回 None。"""
    text = normalize(id_card)
    if len(text) != ID_LENGTH or not text[6:14].isdigit():
        return None
    year, month, day = int(text[6:10]), int(text[10:12]), int(text[12:14])
    try:
        return date(year, month, day)
    except ValueError:
        return None


def gender_of(id_card: str) -> Optional[str]:
    """性别：第 17 位奇数为男、偶数为女。"""
    text = normalize(id_card)
    if len(text) != ID_LENGTH or not text[16].isdigit():
        return None
    return "男" if int(text[16]) % 2 == 1 else "女"


def province_of(id_card: str) -> Optional[str]:
    """前 6 位省级行政区划码对应的省份名；无效返回 None。"""
    text = normalize(id_card)
    if len(text) < 2 or not text[:2].isdigit():
        return None
    return PROVINCE_CODES.get(text[:2])


def age_at(birth: date, on_date: date) -> int:
    """周岁年龄：以 on_date 为基准（未过生日则减一岁）。"""
    years = on_date.year - birth.year
    if (on_date.month, on_date.day) < (birth.month, birth.day):
        years -= 1
    return years


@dataclass
class IDCardCheck:
    """一次身份证校验的完整结论。"""

    raw: str = ""
    normalized: str = ""
    format_ok: bool = False
    checksum_ok: bool = False
    birthdate: Optional[date] = None
    gender: str = ""
    province: str = ""
    errors: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """结构层面是否可用（格式 + 校验位 + 出生日期）。"""
        return self.format_ok and self.checksum_ok and self.birthdate is not None

    @property
    def detail(self) -> str:
        return "；".join(self.errors)


def validate_structure(id_card) -> IDCardCheck:
    """结构校验：格式 → 校验位 → 出生日期 → 地区码（不涉及保险期间）。"""
    text = normalize(id_card)
    check = IDCardCheck(raw="" if id_card is None else str(id_card), normalized=text)

    if not text:
        check.errors.append("身份证号为空")
        return check

    check.format_ok = check_format(text)
    if not check.format_ok:
        check.errors.append(
            f"格式不正确（应为 18 位，实际 {len(text)} 位）")

    check.checksum_ok = check_checksum(text)
    if not check.checksum_ok:
        check.errors.append("校验位不通过（第 18 位与前 17 位算出的校验码不符）")

    if check.format_ok:  # 长度对才解析得出日期
        check.birthdate = parse_birthdate(text)
        if check.birthdate is None:
            check.errors.append(
                f"出生日期非法（第 7-14 位为 {text[6:14]}）")
        check.gender = gender_of(text) or ""
        province = province_of(text)
        check.province = province or ""
        if province is None:
            check.errors.append(f"地区码无效（前 2 位为 {text[:2]}）")

    return check
