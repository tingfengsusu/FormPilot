"""batch/runner.py 测试（Mock 模式，无浏览器）。

覆盖：
  - 串行执行「已确认」记录 → 置「待复核」，写审计
  - 回读不一致 / 必填缺失 → 「录入失败」+ 原因；重试到上限
  - 起期按**录入当天**再取平（导入时算好的起期可能已过期）
  - 中断恢复：卡在「录入中」的记录先重置为「已确认」
  - 品种配置缺字段映射 → 不开始录入（避免填半张表还置「待复核」）
  - 非「已确认」的记录不动
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card, use_temp_key

# 本文件只测执行逻辑：强制 Mock 模式，**不打开浏览器、不访问真实投保页**
from core import config as config_module

config_module.config._data = {"mock_mode": True}

from batch.db import BatchDB
from batch.models import (STATUS_CONFIRMED, STATUS_DATA_ERROR,
                          STATUS_FILL_FAILED, STATUS_FILLING, STATUS_PENDING,
                          STATUS_TO_REVIEW, Applicant)
from batch.recipe import Recipe
from batch.runner import BatchRunner

STUDENT_ID = make_id_card("410305", "20150901", "12")


def _recipe(required_properties=None, submit=False, max_retries=1) -> Recipe:
    recipe = Recipe.load()
    recipe.execute["required_properties"] = list(required_properties or [])
    recipe.execute["submit"] = submit
    recipe.execute["max_retries"] = max_retries
    recipe.execute["retry_backoff_seconds"] = 0
    recipe.execute["interval_seconds"] = 0
    recipe.input_config["period_start_floor"] = "off"
    return recipe


def _applicant(**kwargs) -> Applicant:
    values = dict(
        batch_id=1, name="学生甲", id_card=STUDENT_ID, phone="13800000000",
        payer_name="家长甲", payer_phone="13900000000",
        school="柳泉小学", grade="1年级", class_name="1班",
        student_level="小学生",
        period_start="2026-08-22", period_end="2027-08-21",
    )
    values.update(kwargs)
    return Applicant(**values)


@contextlib.contextmanager
def _db(tmp_dir: str):
    use_temp_key(tmp_dir)
    db = BatchDB(os.path.join(tmp_dir, "t.db"))
    try:
        yield db
    finally:
        db.close()


def _add(db: BatchDB, **kwargs):
    batch = db.create_batch("测试批次")
    applicant = _applicant(batch_id=batch.id, **kwargs)
    applicant.id = db.add_applicant(applicant)
    return batch, applicant


def _confirm(db: BatchDB, applicant) -> None:
    db.set_status(applicant.id, STATUS_CONFIRMED, operator="人工")


def test_run_marks_to_review():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add(db)
            _confirm(db, applicant)
            runner = BatchRunner(db, recipe=_recipe(), screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.total == 1 and report.to_review == 1 and report.failed == 0
            assert report.records[0].ok
            assert db.get_applicant(applicant.id).status == STATUS_TO_REVIEW
            history = [h["to_status"] for h in db.status_history(applicant.id)]
            assert history == [STATUS_PENDING, STATUS_CONFIRMED, STATUS_FILLING,
                               STATUS_TO_REVIEW]
            # Mock 模式没有页面，不产出截图
            assert report.records[0].screenshot == ""


def test_failed_record_keeps_reason_and_retries():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add(db, payer_name="")   # 监护人姓名缺失 → missing
            _confirm(db, applicant)
            runner = BatchRunner(db, recipe=_recipe(max_retries=2), screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.failed == 1 and report.to_review == 0
            record = report.records[0]
            assert record.attempts == 3              # 1 次 + 重试 2 次
            assert "监护人姓名" in record.reason
            stored = db.get_applicant(applicant.id)
            assert stored.status == STATUS_FILL_FAILED
            assert "监护人姓名" in stored.fail_reason
            assert "重试 3 次" in db.status_history(applicant.id)[-1]["note"]


def test_period_refreshed_to_fill_day():
    """导入时算好的起期到录入时可能已过期 → 按录入当天再取平一次。"""
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add(db)
            _confirm(db, applicant)
            recipe = _recipe()
            recipe.input_config["period_start_floor"] = "today"
            runner = BatchRunner(db, recipe=recipe, screenshot_dir=td,
                                 today=date(2026, 10, 1))
            report = runner.run(batch.id)

            stored = db.get_applicant(applicant.id)
            assert stored.period_start == "2026-10-01"      # 按录入当天取平
            assert stored.period_end == "2027-09-30"        # 保持 364 天
            assert "起期按页面约束调整" in report.records[0].period_note


def test_reset_stuck_filling():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add(db)
            db.set_status(applicant.id, STATUS_CONFIRMED)
            db.set_status(applicant.id, STATUS_FILLING)     # 模拟进程被杀
            runner = BatchRunner(db, recipe=_recipe(), screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.reset_stuck == 1
            assert report.to_review == 1
            assert db.get_applicant(applicant.id).status == STATUS_TO_REVIEW


def test_skips_unconfirmed_and_other_statuses():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, pending = _add(db)                       # 未确认
            _, error = _add(db, name="学生乙")               # 数据异常
            db.set_status(error.id, STATUS_DATA_ERROR)
            runner = BatchRunner(db, recipe=_recipe(), screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.total == 0
            assert db.get_applicant(pending.id).status == STATUS_PENDING
            assert db.get_applicant(error.id).status == STATUS_DATA_ERROR
            assert "没有待录入的记录" in report.notes[0]


def test_incomplete_product_mapping_blocks_run():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add(db)
            _confirm(db, applicant)
            recipe = _recipe(required_properties=["不存在的属性"])
            runner = BatchRunner(db, recipe=recipe, screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.skipped == 1 and report.to_review == 0
            assert "品种配置不完整" in report.notes[0]
            # 状态没被动过（不半途而废）
            assert db.get_applicant(applicant.id).status == STATUS_CONFIRMED


def test_screenshot_naming():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add(db, name='学生/甲:1')     # 非法文件名字符
            runner = BatchRunner(db, recipe=_recipe(), screenshot_dir=td)
            path = runner._screenshot(None, applicant)
            assert path == ""                                # 无页面 → 不产截图
            assert os.path.isdir(os.path.join(td, str(batch.id))) is False


if __name__ == "__main__":
    test_run_marks_to_review()
    test_failed_record_keeps_reason_and_retries()
    test_period_refreshed_to_fill_day()
    test_reset_stuck_filling()
    test_skips_unconfirmed_and_other_statuses()
    test_incomplete_product_mapping_blocks_run()
    test_screenshot_naming()
    print("test_batch_runner.py 全部通过 ✓")
