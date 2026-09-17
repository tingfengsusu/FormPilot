"""规则引擎测试（年级解析 + 方案推荐 + Filler 集成）。"""
from __future__ import annotations

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.product import Product
from core.property_pool import PropertyPool
from core.rule_engine import (RuleEngine, grade_from_birth, grade_from_class,
                              recommend_by_grade)


def test_grade_from_class():
    assert grade_from_class("三年级二班") == 3
    assert grade_from_class("6年级") == 6
    assert grade_from_class("十二年级") == 12
    assert grade_from_class("三年级") == 3
    assert grade_from_class("毕业班") is None
    assert grade_from_class("") is None
    assert grade_from_class(None) is None


def test_grade_from_birth():
    # 2015-09-01 出生：9月1日超过8月31日截止 → 2022 年入学 → 2026 秋为五年级
    assert grade_from_birth("2015-09-01", today=date(2026, 9, 13)) == 5
    # 2019-03-10：8月31日前满6岁 → 2025 年入学 → 2026 秋为二年级
    assert grade_from_birth("2019-03-10", today=date(2026, 9, 13)) == 2
    # 2020-05-01：2026 年入学 → 一年级
    assert grade_from_birth("2020-05-01", today=date(2026, 9, 13)) == 1
    # 春季学期同样成立：2026 春（2025 学年）入学 2025 → 二年级
    assert grade_from_birth("2019-03-10", today=date(2026, 3, 10)) == 2
    assert grade_from_birth("2015年9月1日", today=date(2026, 9, 13)) == 5
    assert grade_from_birth("bad", today=date(2026, 9, 13)) is None
    assert grade_from_birth(None) is None


def test_recommend_by_grade():
    pool = PropertyPool()
    # 班级优先
    pool.feed_dict({"班级": "三年级二班", "出生日期": "2015-09-01"})
    assert recommend_by_grade(pool) == "基础版（100元）"  # 1~3 年级

    pool2 = PropertyPool()
    pool2.feed_dict({"出生日期": "2015-09-01"})  # 五年级
    assert recommend_by_grade(pool2, today=date(2026, 9, 13)) == "加强版（150元）"

    pool3 = PropertyPool()
    pool3.feed_dict({"班级": "初一（3）班"})  # 无法解析 → 靠出生日期
    pool3.feed_dict({"出生日期": "2011-05-20"})  # 2017 入学 → 初二(8)
    assert recommend_by_grade(pool3, today=date(2026, 9, 13)) == "尊享版（200元）"

    # 都没有 → None
    assert recommend_by_grade(PropertyPool()) is None


def test_rule_engine_apply():
    pool = PropertyPool()
    pool.feed_dict({"班级": "五年级一班"})
    assert RuleEngine.apply("recommend_by_grade", pool) == "加强版（150元）"
    assert RuleEngine.apply("no_such_rule", pool) is None


def test_filler_mock_uses_rule_when_blank():
    """属性池缺 保险方案 时，规则推荐补值并写进报告与池子。"""
    product = Product.from_dict({
        "name": "测试",
        "field_mapping": [
            {"property": "班级", "locator": 'input[name="bj"]', "required": False},
            {"property": "保险方案", "locator": 'select[name="baoxian"]',
             "rule": "recommend_by_grade", "required": True},
        ],
    })
    pool = PropertyPool()
    pool.feed_dict({"班级": "三年级二班"})

    from page.filler import FormFiller
    report = FormFiller(product, pool)._mock_fill()

    statuses = {r["property"]: r["status"] for r in report}
    assert statuses["保险方案"] == "filled"
    entry = next(r for r in report if r["property"] == "保险方案")
    assert entry["value"] == "基础版（100元）"
    assert "recommend_by_grade" in entry.get("message", "")
    # 规则结果回喂池子（供回读校验/后续使用）
    assert pool.get("保险方案") == "基础版（100元）"


def test_filler_explicit_value_wins_over_rule():
    """用户显式提供的值不被规则覆盖。"""
    product = Product.from_dict({
        "name": "测试",
        "field_mapping": [
            {"property": "班级", "locator": 'input[name="bj"]', "required": False},
            {"property": "保险方案", "locator": 'select[name="baoxian"]',
             "rule": "recommend_by_grade", "required": True},
        ],
    })
    pool = PropertyPool()
    pool.feed_dict({"班级": "三年级二班", "保险方案": "尊享版（200元）"})

    from page.filler import FormFiller
    report = FormFiller(product, pool)._mock_fill()
    entry = next(r for r in report if r["property"] == "保险方案")
    assert entry["value"] == "尊享版（200元）"
    assert "message" not in entry  # 没走规则


if __name__ == "__main__":
    test_grade_from_class()
    test_grade_from_birth()
    test_recommend_by_grade()
    test_rule_engine_apply()
    test_filler_mock_uses_rule_when_blank()
    test_filler_explicit_value_wins_over_rule()
    print("test_rule_engine.py 全部通过 ✓")
