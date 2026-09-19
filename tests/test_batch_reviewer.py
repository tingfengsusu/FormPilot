"""batch/reviewer.py 测试（缺数据时也要能复核）。

覆盖：
  - 队列：只取「待复核」，异常优先排序
  - 通过 → 已复核；打回 → 已确认（都写审计）
  - 批量通过只批**没有异常项**的记录
  - 汇总 / 失败清单 / 异常清单
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card, use_temp_key

from batch.db import BatchDB
from batch.models import (STATUS_CONFIRMED, STATUS_DATA_ERROR,
                          STATUS_FILL_FAILED, STATUS_FILLING, STATUS_PAID,
                          STATUS_PENDING, STATUS_REVIEWED, STATUS_TO_REVIEW,
                          Applicant, ValidationResult)
from batch.reviewer import MODE_ORDER, MODE_EXCEPTION_FIRST, Reviewer

STUDENT_ID = make_id_card("410305", "20150901", "12")


@contextlib.contextmanager
def _db(tmp_dir: str):
    use_temp_key(tmp_dir)
    db = BatchDB(os.path.join(tmp_dir, "t.db"))
    try:
        yield db
    finally:
        db.close()


def _applicant(batch_id: int, **kwargs) -> Applicant:
    values = dict(batch_id=batch_id, name="学生甲", id_card=STUDENT_ID,
                  phone="13800000000", school="柳泉小学", grade="1年级",
                  class_name="1班", student_level="小学生",
                  period_start="2026-09-19", period_end="2027-09-18")
    values.update(kwargs)
    return Applicant(**values)


def _to_review(db: BatchDB, batch_id: int, name: str = "学生甲",
               with_issue: bool = False) -> int:
    applicant = _applicant(batch_id, name=name)
    applicant.id = db.add_applicant(applicant)
    db.set_status(applicant.id, STATUS_CONFIRMED)
    db.set_status(applicant.id, STATUS_FILLING)      # 与 runner 的真实路径一致
    db.set_status(applicant.id, STATUS_TO_REVIEW)
    db.replace_validations(applicant.id, [
        ValidationResult(applicant_id=applicant.id, field="id_card",
                         rule="checksum", passed=True, detail="校验位通过"),
        ValidationResult(applicant_id=applicant.id, field="school",
                         rule="dictionary", passed=not with_issue,
                         detail="字典未命中" if with_issue else "匹配到「柳泉小学」"),
    ])
    return applicant.id


def test_queue_exception_first():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("复核测试")
            clean_id = _to_review(db, batch.id, "无异常")
            issue_id = _to_review(db, batch.id, "有异常", with_issue=True)

            reviewer = Reviewer(db)
            items = reviewer.queue(batch.id)
            assert [i.applicant.id for i in items] == [issue_id, clean_id]
            assert items[0].clean is False and items[1].clean is True
            assert items[0].issues and "字典未命中" in items[0].issues[0].detail

            # 顺序模式：按 id
            ordered = reviewer.queue(batch.id, mode=MODE_ORDER)
            assert [i.applicant.id for i in ordered] == [clean_id, issue_id]
            # 只取「待复核」：把一条改成已复核后不再进队列
            reviewer.approve(clean_id)
            assert [i.applicant.id for i in reviewer.queue(batch.id)] == [issue_id]


def test_approve_and_reject_write_audit():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("复核测试")
            first = _to_review(db, batch.id, "学生甲")
            second = _to_review(db, batch.id, "学生乙")
            reviewer = Reviewer(db, )
            reviewer.approve(first, operator="张三", note="截图无误")
            reviewer.reject(second, operator="张三", note="学校填错了")

            assert db.get_applicant(first).status == STATUS_REVIEWED
            assert db.get_applicant(second).status == STATUS_CONFIRMED

            history = db.status_history(first)
            assert history[-1]["to_status"] == STATUS_REVIEWED
            assert history[-1]["operator"] == "张三"
            assert history[-1]["note"] == "截图无误"
            assert db.status_history(second)[-1]["to_status"] == STATUS_CONFIRMED

            # 已复核 → 已支付（人工）；复核不碰支付
            db.set_status(first, STATUS_PAID, operator="张三", note="已缴费")
            assert db.count_by_status(batch.id)[STATUS_PAID] == 1


def test_bulk_approve_only_clean_records():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("批量通过")
            for index in range(3):
                _to_review(db, batch.id, f"无异常{index}")
            issue_id = _to_review(db, batch.id, "有异常", with_issue=True)

            reviewer = Reviewer(db)
            approved = reviewer.bulk_approve(batch.id, operator="李四")
            assert approved == 3
            assert db.get_applicant(issue_id).status == STATUS_TO_REVIEW
            counts = reviewer.summary(batch.id)
            assert counts[STATUS_REVIEWED] == 3 and counts[STATUS_TO_REVIEW] == 1
            # 汇总包含所有状态（含 0），便于界面直接展示
            assert counts[STATUS_PENDING] == 0 and counts[STATUS_DATA_ERROR] == 0


def test_failed_and_exception_lists():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("清单")
            failed = _applicant(batch.id, name="失败学生")
            failed.id = db.add_applicant(failed)
            db.set_status(failed.id, STATUS_CONFIRMED)
            db.set_status(failed.id, STATUS_FILLING)
            db.set_status(failed.id, STATUS_FILL_FAILED,
                          fail_reason="[监护人电话] 必填缺失")
            error = _applicant(batch.id, name="异常学生")
            error.id = db.add_applicant(error)
            db.set_status(error.id, STATUS_DATA_ERROR)
            db.replace_validations(error.id, [
                ValidationResult(applicant_id=error.id, field="school",
                                 rule="dictionary", passed=False,
                                 detail="「清花园」不在页面选项/别名表中")])

            reviewer = Reviewer(db)
            failures = reviewer.failed_items(batch.id)
            assert len(failures) == 1 and "监护人电话" in failures[0]["reason"]

            exceptions = reviewer.exception_items(batch.id)
            assert len(exceptions) == 1
            assert exceptions[0]["applicant_name"] == "异常学生"
            assert "清花园" in exceptions[0]["detail"]


if __name__ == "__main__":
    test_queue_exception_first()
    test_approve_and_reject_write_audit()
    test_bulk_approve_only_clean_records()
    test_failed_and_exception_lists()
    print("test_batch_reviewer.py 全部通过 ✓")
