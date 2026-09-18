"""规则引擎 — 品种 yaml 中 rule 字段的执行器。

品种的字段映射可以声明 rule（如 保险方案 的 rule: recommend_by_grade），
表示「属性池里没有这个值时，由规则计算推荐」。规则只补空，不覆盖用户
显式提供的值；计算结果会回喂属性池，从而同样参与回读校验。

当前实现的规则：
  recommend_by_grade —— 按年级推荐保险方案：
      优先从「班级」解析年级（如 三年级二班 → 3），否则用「出生日期」
      按入学年龄推算（满6周岁入学，截止8月31日）。
      1~3 年级 → 基础版   4~6 年级 → 加强版   7 年级及以上 → 尊享版
      （学前班按一年级处理）

注意：方案档位文案与 mock 页面选项一致；对接真实页面时如选项文案不同，
改 GRADE_PLAN_MAP 即可。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Callable, Dict, Optional

from core.property_pool import PropertyPool

#: 年级区间 → 方案档位（文案与测试页 select 的 option 文本一致）。
GRADE_PLAN_MAP = [
    (1, 3, "基础版（100元）"),
    (4, 6, "加强版（150元）"),
    (7, 12, "尊享版（200元）"),
]

_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_to_int(text: str) -> Optional[int]:
    """中文数字转整数（支持 一~十二）；无法解析返回 None。"""
    text = text.strip()
    if not text:
        return None
    if text in _CN_DIGITS:
        return _CN_DIGITS[text]
    # 十一 ~ 十九 / 二十 等
    if "十" in text:
        parts = text.split("十")
        tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
        ones = _CN_DIGITS.get(parts[1], 0) if len(parts) > 1 and parts[1] else 0
        if parts and all(p in _CN_DIGITS or p == "" for p in parts):
            return tens * 10 + ones
    return None


def grade_from_class(text: Any) -> Optional[int]:
    """从班级文本解析年级（如 三年级二班 → 3、6年级 → 6）。"""
    if not text:
        return None
    m = re.search(r"([一二三四五六七八九十]{1,3}|\d{1,2})\s*年级", str(text))
    if not m:
        return None
    token = m.group(1)
    if token.isdigit():
        return int(token)
    return _cn_to_int(token)


def grade_from_birth(birth: Any, today: Optional[date] = None) -> Optional[int]:
    """从出生日期推算当前年级。

    按满 6 周岁入学、截止 8 月 31 日计算：2015-09-01 出生 → 2022 年秋
    入学 → 2026 年秋为五年级。无法解析返回 None。
    """
    if not birth:
        return None
    m = re.search(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})", str(birth))
    if not m:
        return None
    year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
    today = today or date.today()

    # 入学年份：当年 8 月 31 日前满 6 周岁则当年入学
    enroll_year = year + 6 if (month, day) <= (8, 31) else year + 7
    grade = today.year - enroll_year + 1
    return grade if grade >= -1 else None  # -1 表示托班/学前极低龄


def recommend_by_grade(pool: PropertyPool,
                       today: Optional[date] = None) -> Optional[str]:
    """按年级推荐保险方案档位；年级无法确定时返回 None。

    年级来源优先级：显式「年级」属性 → 「班级」解析 → 「出生日期」推算。
    显式的「年级」属性是批量投保场景新增的（台账里有独立的年级列，
    如「五年级」，比从班级号或出生日期推算更可靠）。
    """
    grade = grade_from_class(pool.get("年级"))
    if grade is None:
        grade = grade_from_class(pool.get("班级"))
    if grade is None:
        grade = grade_from_birth(pool.get("出生日期"), today)
    if grade is None:
        return None
    if grade < 1:
        grade = 1   # 学前班按一年级
    if grade > 12:
        grade = 12
    for lo, hi, plan in GRADE_PLAN_MAP:
        if lo <= grade <= hi:
            return plan
    return None


class RuleEngine:
    """规则执行器：rule 名 → 计算函数。"""

    _RULES: Dict[str, Callable[[PropertyPool], Optional[str]]] = {
        "recommend_by_grade": recommend_by_grade,
    }

    @classmethod
    def apply(cls, rule_name: str, pool: PropertyPool) -> Optional[str]:
        """执行规则，返回推荐值；规则不存在或无法计算返回 None。"""
        func = cls._RULES.get(rule_name)
        if func is None:
            return None
        try:
            return func(pool)
        except Exception:  # noqa: BLE001 — 规则失败不应中断填表
            return None
