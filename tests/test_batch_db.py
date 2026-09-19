"""batch/db.py + models.py + crypto.py + recipe.py 测试（纯逻辑，无浏览器）。

覆盖：
  - 状态机：合法/非法迁移、审计留痕、status_log 不记身份证号
  - 中断恢复：录入中 → 已确认
  - 重复投保查询、敏感字段加密入库与脱敏清理
  - Recipe：validate 段解析、property_map、ref_year、配方清单
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card, use_temp_key

from batch import crypto
from batch.db import BatchDB
from batch.models import (STATUS_CONFIRMED, STATUS_DATA_ERROR, STATUS_FILLING,
                          STATUS_PAID, STATUS_PENDING, STATUS_TO_REVIEW,
                          STATUS_REVIEWED, Applicant, StatusTransitionError,
                          assert_transition, can_transition)
from batch.recipe import Recipe

STUDENT_ID = make_id_card("410305", "20150901", "12")


@contextlib.contextmanager
def _db(tmp_dir: str):
    use_temp_key(tmp_dir)
    db = BatchDB(os.path.join(tmp_dir, "t.db"))
    try:
        yield db
    finally:
        db.close()


def _applicant(**kwargs) -> Applicant:
    values = dict(batch_id=1, name="学生甲", id_card=STUDENT_ID,
                  phone="13800000000", school="柳集小学", class_name="1班",
                  period_start="2026-08-22", period_end="2027-08-21",
                  payer_id_card=make_id_card("410305", "19850312", "34", "女"))
    values.update(kwargs)
    return Applicant(**values)


# ----------------------------------------------------------------------
# 状态机
# ----------------------------------------------------------------------
def test_transitions():
    assert can_transition(None, STATUS_PENDING) is True
    assert can_transition(None, STATUS_PAID) is False
    assert can_transition(STATUS_PENDING, STATUS_CONFIRMED) is True
    assert can_transition(STATUS_CONFIRMED, STATUS_FILLING) is True
    assert can_transition(STATUS_FILLING, STATUS_TO_REVIEW) is True
    assert can_transition(STATUS_TO_REVIEW, STATUS_REVIEWED) is True
    assert can_transition(STATUS_REVIEWED, STATUS_PAID) is True
    assert can_transition(STATUS_PENDING, STATUS_PAID) is False
    assert can_transition(STATUS_PAID, STATUS_REVIEWED) is False
    assert can_transition(STATUS_PENDING, STATUS_PENDING) is True   # 幂等

    try:
        assert_transition(STATUS_PENDING, STATUS_PAID)
        assert False, "非法迁移应抛错"
    except StatusTransitionError:
        pass


def test_status_change_writes_audit_without_id_card():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("测试批次", source_file="x.xlsx")
            applicant = _applicant(batch_id=batch.id)
            applicant.id = db.add_applicant(applicant)

            db.set_status(applicant.id, STATUS_CONFIRMED, operator="张三",
                          note="客户确认投保")
            db.set_status(applicant.id, STATUS_FILLING)
            db.set_status(applicant.id, STATUS_TO_REVIEW, note="填写完成")

            assert db.get_applicant(applicant.id).status == STATUS_TO_REVIEW
            history = db.status_history(applicant.id)
            assert [h["to_status"] for h in history] == [
                STATUS_PENDING, STATUS_CONFIRMED, STATUS_FILLING, STATUS_TO_REVIEW]
            assert history[1]["operator"] == "张三" and history[1]["note"] == "客户确认投保"
            # 审计里不得出现身份证号（设计文档第七节）
            assert all(STUDENT_ID not in str(h) for h in history)

            try:
                db.set_status(applicant.id, STATUS_PAID)
                assert False, "待复核 → 已支付 应被状态机拒绝"
            except StatusTransitionError:
                pass


def test_reset_stuck_filling():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("中断恢复")
            applicant = _applicant(batch_id=batch.id)
            applicant.id = db.add_applicant(applicant)
            db.set_status(applicant.id, STATUS_CONFIRMED)
            db.set_status(applicant.id, STATUS_FILLING)

            assert db.reset_stuck_filling(batch.id) == 1
            assert db.get_applicant(applicant.id).status == STATUS_CONFIRMED
            assert db.reset_stuck_filling(batch.id) == 0


def test_count_by_status_and_duplicates():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("重复检测")
            first = _applicant(batch_id=batch.id)
            first.id = db.add_applicant(first)
            second = _applicant(batch_id=batch.id, name="学生乙")
            second.id = db.add_applicant(second)

            groups = db.find_duplicates(batch.id)
            assert len(groups) == 1 and set(groups[0]["applicant_ids"]) == {
                first.id, second.id}

            db.set_status(first.id, STATUS_DATA_ERROR, fail_reason="重复投保")
            counts = db.count_by_status(batch.id)
            assert counts[STATUS_DATA_ERROR] == 1 and counts[STATUS_PENDING] == 1
            assert counts[STATUS_PAID] == 0


# ----------------------------------------------------------------------
# 加密与脱敏
# ----------------------------------------------------------------------
def test_crypto_roundtrip_and_mask():
    with tempfile.TemporaryDirectory() as td:
        use_temp_key(td)
        token = crypto.encrypt(STUDENT_ID)
        assert token.startswith("enc:") and STUDENT_ID not in token
        assert crypto.decrypt(token) == STUDENT_ID
        assert crypto.encrypt(token) == token            # 不二次加密
        assert crypto.decrypt("明文历史值") == "明文历史值"   # 兼容旧库

        masked = crypto.mask_id_card(STUDENT_ID)
        assert masked.startswith(STUDENT_ID[:6]) and masked.endswith(STUDENT_ID[-4:])
        assert STUDENT_ID not in masked
        assert crypto.mask_phone("13800000000") == "138****0000"


def test_clear_sensitive():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("脱敏")
            applicant = _applicant(batch_id=batch.id)
            applicant.id = db.add_applicant(applicant)

            assert db.clear_batch_sensitive(batch.id) == 1
            assert db.clear_batch_sensitive(batch.id) == 0     # 幂等：已脱敏的跳过
            cleared = db.get_applicant(applicant.id)
            assert cleared.id_card != STUDENT_ID
            assert "****" in cleared.id_card or "*" in cleared.id_card
            assert cleared.status == STATUS_PENDING            # 状态不变
            assert "脱敏" in db.status_history(applicant.id)[-1]["note"]
            # 密文列里也不应再出现完整身份证号
            with contextlib.closing(sqlite3.connect(db.path)) as conn:
                raw = conn.execute("SELECT id_card FROM applicant").fetchone()[0]
            assert STUDENT_ID not in raw


# ----------------------------------------------------------------------
# Recipe
# ----------------------------------------------------------------------
def test_recipe_load_and_overrides():
    recipe = Recipe.load()
    assert recipe.name == "学平险批量投保"
    assert recipe.ref_year == 2026
    assert recipe.input_type == "excel"
    assert recipe.applicant_name_mode() == "current_operator"
    assert recipe.required_in_multi_user("applicant_name") is True

    required = recipe.validate_spec.required
    assert "name" in required and "school" in required and "period_end" in required
    assert tuple(recipe.validate_spec.age_range) == (5, 20)
    assert tuple(recipe.validate_spec.period_days) == (360, 370)
    assert recipe.property_map["学生姓名"] == "name"
    assert recipe.product_name == "太保洛阳学平险_基础"
    assert "学平险" in Recipe.list_recipes()

    validator = recipe.build_validator()
    assert validator.required == required


if __name__ == "__main__":
    test_transitions()
    test_status_change_writes_audit_without_id_card()
    test_reset_stuck_filling()
    test_count_by_status_and_duplicates()
    test_crypto_roundtrip_and_mask()
    test_clear_sensitive()
    test_recipe_load_and_overrides()
    print("test_batch_db.py 全部通过 ✓")
