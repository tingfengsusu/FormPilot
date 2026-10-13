"""批量执行端到端：**真浏览器 + 真状态机 + 本地 Mock 页面**。

验证 P1 重构后的执行路径（engine 接管浏览器会话）在真实链路上行为不变：

    已确认 → 录入中 → 待复核（截图落盘）
    已确认 → 录入中 → 录入失败（原因里带字段名）

页面是本地 ``tests/mock_form.html``（file://），**不访问任何外部系统**；
未装 Playwright 或浏览器内核未下载时自动跳过（计为跳过而非失败）。
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import browser_available, make_id_card, skip_test, use_temp_key

# 本文件要真开浏览器：显式关掉 Mock 模式（与 test_mock_full_page.py 同做法）
from core import config as config_module

config_module.config._data = {"mock_mode": False}

from batch.db import BatchDB
from batch.models import (STATUS_CONFIRMED, STATUS_FILL_FAILED, STATUS_FILLING,
                          STATUS_PENDING, STATUS_TO_REVIEW, Applicant)
from batch.recipe import Recipe
from batch.runner import BatchRunner

STUDENT_ID = make_id_card("410305", "20150901", "12")

#: 指向本地 mock 页面的品种（config/product_library/）
_MOCK_PRODUCT = "学平险_本地Mock测试"


def _recipe() -> Recipe:
    recipe = Recipe.load()
    recipe.execute["product"] = _MOCK_PRODUCT
    recipe.execute["required_properties"] = [
        "学生姓名", "学生身份证号", "监护人电话", "保险方案"]
    recipe.execute["submit"] = False
    recipe.execute["max_retries"] = 0
    recipe.execute["retry_backoff_seconds"] = 0
    recipe.execute["interval_seconds"] = 0
    recipe.execute["headless"] = True          # 无人值守跑，不开窗口
    recipe.input_config["period_start_floor"] = "off"
    return recipe


def _applicant(**kwargs) -> Applicant:
    values = dict(
        batch_id=1, name="学生甲", id_card=STUDENT_ID, phone="13800000000",
        payer_name="家长甲", payer_phone="13900000000",
        school="柳泉小学", grade="三年级", class_name="三年级二班",
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


def _add_confirmed(db: BatchDB, **kwargs):
    batch = db.create_batch("端到端")
    applicant = _applicant(batch_id=batch.id, **kwargs)
    applicant.id = db.add_applicant(applicant)
    db.set_status(applicant.id, STATUS_CONFIRMED, operator="人工")
    return batch, applicant


def test_batch_e2e_real_browser_marks_to_review():
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add_confirmed(db)
            runner = BatchRunner(db, recipe=_recipe(), screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.to_review == 1 and report.failed == 0, report.notes
            record = report.records[0]
            assert record.ok and record.attempts == 1
            # 真页面截图真的落盘了（Mock 模式覆盖不到这一段）
            assert record.screenshot and os.path.exists(record.screenshot)

            stored = db.get_applicant(applicant.id)
            assert stored.status == STATUS_TO_REVIEW
            assert stored.screenshot == record.screenshot
            history = [h["to_status"] for h in db.status_history(applicant.id)]
            assert history == [STATUS_PENDING, STATUS_CONFIRMED,
                               STATUS_FILLING, STATUS_TO_REVIEW]


def test_batch_e2e_real_browser_failure_keeps_reason():
    if not browser_available():
        skip_test("浏览器不可用（未装 Playwright 或内核未下载）")
        return

    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add_confirmed(db, name="")     # 必填缺失
            runner = BatchRunner(db, recipe=_recipe(), screenshot_dir=td)
            report = runner.run(batch.id)

            assert report.failed == 1 and report.to_review == 0
            assert not report.records[0].screenshot           # 失败不产截图
            stored = db.get_applicant(applicant.id)
            assert stored.status == STATUS_FILL_FAILED
            assert "学生姓名" in stored.fail_reason


if __name__ == "__main__":
    test_batch_e2e_real_browser_marks_to_review()
    test_batch_e2e_real_browser_failure_keeps_reason()
    print("test_batch_engine_e2e.py 全部通过 ✓")
