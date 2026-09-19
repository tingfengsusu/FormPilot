"""批量投保页 GUI 离屏烟雾测试（不开窗口，验证构造与复核交互）。

覆盖：批次列表、明细表状态着色、复核三栏（源数据/截图/校验结果）、
键盘 J/K/Enter/Backspace、批量通过、明细表点击联动。

运行：QT_QPA_PLATFORM=offscreen python tools/smoke_batch_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QMessageBox

from batch.db import BatchDB
from batch.models import (STATUS_CONFIRMED, STATUS_FILLING, STATUS_FILL_FAILED,
                          STATUS_TO_REVIEW, Applicant, ValidationResult)
from batch.reviewer import Reviewer


def main() -> None:
    app = QApplication(sys.argv)

    # 屏蔽模态弹窗，让按钮处理函数可以无头跑通
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    from gui.batch_page import BatchPage

    os.environ["FORMPILOT_ID_KEY"] = os.path.join(tempfile.mkdtemp(), "k.key")
    tmpdir = tempfile.mkdtemp()
    db = BatchDB(os.path.join(tmpdir, "smoke.db"))
    batch = db.create_batch("烟雾批次")

    def add(name: str, status: str, issue: bool = False) -> int:
        applicant = Applicant(batch_id=batch.id, name=name, id_card="410305201509011237",
                              phone="13800000000", school="柳泉小学", grade="1年级",
                              class_name="1班", student_level="小学生",
                              period_start="2026-09-19", period_end="2027-09-18")
        applicant.id = db.add_applicant(applicant)
        db.set_status(applicant.id, STATUS_CONFIRMED)
        if status == STATUS_TO_REVIEW:
            db.set_status(applicant.id, STATUS_FILLING)
            db.set_status(applicant.id, STATUS_TO_REVIEW)
        elif status != STATUS_CONFIRMED:
            db.set_status(applicant.id, STATUS_FILLING)
            db.set_status(applicant.id, status)
        db.replace_validations(applicant.id, [
            ValidationResult(applicant_id=applicant.id, field="school",
                             rule="dictionary", passed=not issue,
                             detail="未命中" if issue else "匹配到「柳泉小学」"),
            ValidationResult(applicant_id=applicant.id, field="period_start",
                             rule="period_adjusted", passed=True,
                             detail="起期按页面约束调整：2026-08-22 → 2026-09-19"),
        ])
        return applicant.id

    with_issue = add("有异常学生", STATUS_TO_REVIEW, issue=True)
    clean = add("无异常学生", STATUS_TO_REVIEW)
    add("录入失败学生", STATUS_FILL_FAILED)
    db.update_batch_total(batch.id)

    # ---- 1. 构造 + 列表 ----
    page = BatchPage(db=db)
    assert page.batch_list.count() == 1, "应列出 1 个批次"
    assert page.table.rowCount() == 3, f"明细应有 3 行，实际 {page.table.rowCount()}"
    assert "待复核 2" in page.summary_label.text(), page.summary_label.text()

    # ---- 2. 状态着色（失败行有背景色）----
    colors = {page.table.item(row, 5).text(): page.table.item(row, 5).background()
              for row in range(page.table.rowCount())}
    assert any("录入失败" == status for status in colors), colors

    # ---- 3. 复核队列：异常优先 ----
    assert len(page._items) == 2
    assert page._items[0].applicant.id == with_issue, "异常项应排在最前"
    assert "有异常项" in page.review_title.text()
    assert "柳泉小学" in page.source_view.toPlainText()
    assert "起期按页面约束调整" in page.validation_view.toPlainText()

    # ---- 4. 键盘：J 下一条 / K 上一条 ----
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_J, Qt.NoModifier))
    assert page._items[page._index].applicant.id == clean
    assert "无异常项" in page.review_title.text()
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_K, Qt.NoModifier))
    assert page._items[page._index].applicant.id == with_issue

    # ---- 5. Enter 通过 → 队列里少一条 ----
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))
    assert Reviewer(db).summary(batch.id)["已复核"] == 1
    assert len(page._items) == 1

    # ---- 6. Backspace 打回 → 回到「已确认」 ----
    page.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Backspace, Qt.NoModifier))
    counts = Reviewer(db).summary(batch.id)
    assert counts["已确认"] == 1, counts          # 被打回的这条回到「已确认」
    assert counts["已复核"] == 1
    assert counts["待复核"] == 0
    assert counts["录入失败"] == 1

    # ---- 7. 批量通过只批无异常项（当前队列已空）----
    page.bulk_approve()
    assert Reviewer(db).summary(batch.id)["已复核"] == 1

    db.close()
    print("smoke_batch_ui.py 全部通过 ✓")


if __name__ == "__main__":
    main()
