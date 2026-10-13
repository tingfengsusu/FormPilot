"""逐条确认（confirm_policy: per_record）与归档对比报告（HTML）。

全部走 Mock 模式（不碰浏览器），文件落在临时目录。

覆盖：
  - 报告渲染：期望/实际/状态三列、HTML 转义、不一致行样式、截图链接
  - 报告落盘：默认路径 + 写入
  - 逐条确认：通过 → 待复核；打回 → 录入失败（原因「人工复核打回」）
  - confirm_policy=batch 时不逐条问；report_html=false 时不产报告
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card, use_temp_key

# 本文件只测流程与报告：强制 Mock 模式，不开浏览器
from core import config as config_module

config_module.config._data = {"mock_mode": True}

from batch.db import BatchDB
from batch.models import (STATUS_CONFIRMED, STATUS_FILL_FAILED,
                          STATUS_TO_REVIEW, Applicant)
from batch.recipe import Recipe
from batch.runner import BatchRunner
from engine.report_html import (default_report_path, records_from_results,
                                render_report, write_report)

STUDENT_ID = make_id_card("410305", "20150901", "12")


def _recipe(confirm_policy: str = "batch", report_html: bool = True) -> Recipe:
    recipe = Recipe.load()
    recipe.execute["required_properties"] = []
    recipe.execute["submit"] = False
    recipe.execute["max_retries"] = 0
    recipe.execute["retry_backoff_seconds"] = 0
    recipe.execute["interval_seconds"] = 0
    recipe.execute["confirm_policy"] = confirm_policy
    recipe.execute["report_html"] = report_html
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


def _add_confirmed(db: BatchDB):
    batch = db.create_batch("逐条确认")
    applicant = _applicant(batch_id=batch.id)
    applicant.id = db.add_applicant(applicant)
    db.set_status(applicant.id, STATUS_CONFIRMED, operator="人工")
    return batch, applicant


# ----------------------------------------------------------------------
# 报告（纯逻辑）
# ----------------------------------------------------------------------
def test_report_renders_columns_and_escapes():
    records = [{
        "label": "学生甲", "ok": False, "attempts": 1, "screenshot": "shot.png",
        "problems": ["[备注] 回读不一致"],
        "fields": [
            {"property": "姓名", "expected": "张三", "actual": "",
             "status": "filled", "message": ""},
            {"property": "备注", "expected": "<b>期望</b>", "actual": "<i>实际</i>",
             "status": "mismatch", "message": ""},
            {"property": "手机号", "expected": "138", "actual": "",
             "status": "missing", "message": "必填为空"},
        ],
    }]
    html_text = render_report("测试报告", records, meta={"批次": 1})

    assert "张三" in html_text
    assert "<b>期望</b>" not in html_text          # 必须转义，防止值里带标签
    assert "&lt;b&gt;期望&lt;/b&gt;" in html_text
    assert 'class="diff"' in html_text             # 不一致行有样式
    assert 'class="problem"' in html_text          # 缺失行有样式
    assert "shot.png" in html_text                 # 原始截图链接
    assert "共 1 条" in html_text


def test_report_default_path_and_write():
    with tempfile.TemporaryDirectory() as td:
        path = default_report_path(td, "批次")
        assert path.endswith(".html") and os.path.dirname(path) == td
        assert write_report(path, "<html></html>") == path
        assert os.path.exists(path)


def test_records_from_results_carries_structured_diffs():
    class _Result:
        ok = True
        attempts = 2
        screenshot = "s.png"
        problems = []
        report = [
            {"property": "姓名", "status": "filled", "value": "张三"},
            {"property": "备注", "status": "mismatch", "value": "x",
             "expected": "期望值", "actual": "实际值"},
        ]

    records = records_from_results([("甲", _Result())])
    fields = records[0]["fields"]

    assert records[0]["ok"] is True and records[0]["attempts"] == 2
    assert fields[0]["expected"] == "张三"          # filled：期望 = 填进去的值
    assert fields[1]["expected"] == "期望值"        # mismatch：用结构化字段，不解析文案
    assert fields[1]["actual"] == "实际值"


def test_report_filters_machine_tier_but_keeps_problems():
    """档位 machine 的字段不进报告（降级）；出问题的字段照样列出来。"""
    from core.product import Product

    product = Product.from_dict({
        "name": "档位", "target_url": "https://example.com",
        "field_mapping": [
            {"property": "姓名", "locator": "#a", "verify_tier": "machine"},
            {"property": "备注", "locator": "#b", "verify_tier": "machine"},
        ],
    })

    class _Result:
        ok = False
        attempts = 1
        screenshot = ""
        problems = ["[备注] 回读不一致"]
        report = [
            {"property": "姓名", "status": "filled", "value": "张三"},
            {"property": "备注", "status": "mismatch", "value": "x",
             "expected": "期望值", "actual": "实际值"},
        ]

    records = records_from_results([("甲", _Result())], product=product)
    names = [f["property"] for f in records[0]["fields"]]

    assert names == ["备注"]        # 无问题的 machine 字段被过滤，问题字段保留


# ----------------------------------------------------------------------
# 批量流程
# ----------------------------------------------------------------------
def test_per_record_confirm_approve_marks_to_review_and_writes_report():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add_confirmed(db)
            asked = []
            runner = BatchRunner(
                db, recipe=_recipe("per_record"), screenshot_dir=td,
                on_confirm=lambda a, f, s: asked.append(a.id) or True)
            report = runner.run(batch.id)

            assert report.to_review == 1 and asked == [applicant.id]
            assert db.get_applicant(applicant.id).status == STATUS_TO_REVIEW
            assert report.html_report and os.path.exists(report.html_report)
            with open(report.html_report, encoding="utf-8") as f:
                assert "期望" in f.read()


def test_per_record_confirm_reject_marks_failed():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add_confirmed(db)
            runner = BatchRunner(db, recipe=_recipe("per_record"),
                                 screenshot_dir=td,
                                 on_confirm=lambda a, f, s: False)
            report = runner.run(batch.id)

            assert report.failed == 1 and report.to_review == 0
            stored = db.get_applicant(applicant.id)
            assert stored.status == STATUS_FILL_FAILED
            assert "打回" in stored.fail_reason
            assert "打回" in db.status_history(applicant.id)[-1]["note"]


def test_confirm_callback_exception_treated_as_reject():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add_confirmed(db)

            def boom(a, f, s):
                raise RuntimeError("弹窗崩了")

            runner = BatchRunner(db, recipe=_recipe("per_record"),
                                 screenshot_dir=td, on_confirm=boom)
            report = runner.run(batch.id)

            # 回调异常一律按打回 —— 宁可人工再看一次，也不放行
            assert report.failed == 1
            assert db.get_applicant(applicant.id).status == STATUS_FILL_FAILED


def test_exception_only_asks_only_on_failure_and_can_pass_through():
    """仅异常停：正常条不问；失败条问一次，人认了 → 仍进复核队列。"""
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("仅异常停")
            ok_applicant = _applicant(batch_id=batch.id)
            ok_applicant.id = db.add_applicant(ok_applicant)
            bad_applicant = _applicant(batch_id=batch.id, name="学生乙",
                                       payer_name="")          # 必填缺失 → 失败
            bad_applicant.id = db.add_applicant(bad_applicant)
            for aid in (ok_applicant.id, bad_applicant.id):
                db.set_status(aid, STATUS_CONFIRMED, operator="人工")

            asked = []

            def confirmer(applicant, fill, session):
                asked.append((applicant.id, fill.ok))
                return True                                  # 人认了

            runner = BatchRunner(db, recipe=_recipe("exception_only"),
                                 screenshot_dir=td, on_confirm=confirmer)
            report = runner.run(batch.id)

            assert asked == [(bad_applicant.id, False)]      # 只问失败那条
            assert report.failed == 0 and report.to_review == 2
            assert db.get_applicant(bad_applicant.id).status == STATUS_TO_REVIEW


def test_exception_only_reject_keeps_failed():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch = db.create_batch("仅异常停打回")
            applicant = _applicant(batch_id=batch.id, payer_name="")
            applicant.id = db.add_applicant(applicant)
            db.set_status(applicant.id, STATUS_CONFIRMED, operator="人工")

            runner = BatchRunner(db, recipe=_recipe("exception_only"),
                                 screenshot_dir=td,
                                 on_confirm=lambda a, f, s: False)
            report = runner.run(batch.id)

            assert report.failed == 1 and report.to_review == 0
            assert db.get_applicant(applicant.id).status == STATUS_FILL_FAILED


def test_batch_policy_does_not_ask_and_report_can_be_off():
    with tempfile.TemporaryDirectory() as td:
        with _db(td) as db:
            batch, applicant = _add_confirmed(db)
            asked = []
            runner = BatchRunner(db, recipe=_recipe("batch", report_html=False),
                                 screenshot_dir=td,
                                 on_confirm=lambda a, f, s: asked.append(1) or True)
            report = runner.run(batch.id)

            assert report.to_review == 1
            assert asked == []                     # batch 策略不逐条问
            assert report.html_report == ""        # 报告可关


if __name__ == "__main__":
    test_report_renders_columns_and_escapes()
    test_report_default_path_and_write()
    test_records_from_results_carries_structured_diffs()
    test_report_filters_machine_tier_but_keeps_problems()
    test_per_record_confirm_approve_marks_to_review_and_writes_report()
    test_per_record_confirm_reject_marks_failed()
    test_confirm_callback_exception_treated_as_reject()
    test_exception_only_asks_only_on_failure_and_can_pass_through()
    test_exception_only_reject_keeps_failed()
    test_batch_policy_does_not_ask_and_report_can_be_off()
    print("test_batch_confirm_report.py 全部通过 ✓")
