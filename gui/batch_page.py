"""批量投保页：批次 → 明细（状态着色）→ 复核（三栏 + 键盘操作）。

设计文档 5.4 的核心是**降低单条复核时间**：人工只看「截图对不对」，
不重看一遍数据。所以这一页：

    左：批次列表（导入 Excel 入口）      中：明细表（状态着色）
    右：复核区 —— 源数据 / 录入后截图 / 校验结果（异常高亮）
        键盘：J/K 上下条，Enter 通过，Backspace 打回

后台线程：批量录入（runner）跑在 QThread 里，避免界面卡死；
复核与状态变更走 :class:`batch.reviewer.Reviewer`（全部留审计）。
"""
from __future__ import annotations

import os
import threading
from typing import List, Optional

from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog,
                             QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QSplitter, QTableWidget,
                             QTableWidgetItem, QTextEdit, QVBoxLayout,
                             QWidget)

from batch.db import BatchDB
from batch.models import (ALL_STATUSES, STATUS_COLORS, STATUS_DATA_ERROR,
                          STATUS_FILL_FAILED, STATUS_REVIEWED,
                          STATUS_TO_REVIEW)
from batch.recipe import Recipe
from batch.reviewer import ReviewItem, Reviewer
from .theme import mark

_TABLE_COLUMNS = ("id", "姓名", "学校", "年级", "班级", "状态", "来源", "失败/异常原因")


class _RunWorker(QThread):
    """后台执行批量录入（串行填表，可能跑很久）。"""

    finished_report = pyqtSignal(object)
    failed = pyqtSignal(str)
    #: 逐条确认：工作线程请求主线程弹窗（(applicant, fill) → 人作答）
    confirm_requested = pyqtSignal(object, object)

    def __init__(self, runner, batch_id: Optional[int], parent=None):
        super().__init__(parent)
        self._runner = runner
        self._batch_id = batch_id
        self._answer: Optional[bool] = None
        self._answered = threading.Event()

    def run(self):
        try:
            report = self._runner.run(self._batch_id)
            self.finished_report.emit(report)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    # -- 逐条确认（confirm_policy: per_record）--------------------------
    def make_confirmer(self):
        """给 BatchRunner 的 on_confirm：发信号回主线程弹窗，然后**等工作线程这边的答复**。

        GUI 控件只能在主线程动，所以这里不直接弹窗：
        发信号（Qt 会自动排队到主线程）→ 阻塞等待 → 主线程点了按钮再放行。
        """
        def confirmer(applicant, fill, session) -> bool:
            self._answer = None
            self._answered.clear()
            self.confirm_requested.emit(applicant, fill)
            self._answered.wait(timeout=600)     # 兜底：等不到答复按打回处理
            return bool(self._answer)

        return confirmer

    def answer(self, ok: bool) -> None:
        """主线程调用：把人的决定交回工作线程。"""
        self._answer = bool(ok)
        self._answered.set()


class BatchPage(QWidget):
    """批量投保：批次 / 明细 / 复核。"""

    #: 请求返回「保险业务」页
    back_requested = pyqtSignal()

    def __init__(self, db: Optional[BatchDB] = None,
                 recipe: Optional[Recipe] = None, parent=None):
        super().__init__(parent)
        self.recipe = recipe or Recipe.load()
        self.db = db or BatchDB(self.recipe.storage_path("db_path",
                                                         "data/batch.db"))
        self.reviewer = Reviewer(self.db)
        self._items: List[ReviewItem] = []
        self._index = 0
        self._worker: Optional[_RunWorker] = None

        self._build_ui()
        self.refresh_batches()

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addLayout(self._build_header())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_batches())
        splitter.addWidget(self._build_applicants())
        splitter.addWidget(self._build_review())
        splitter.setSizes([240, 460, 520])
        layout.addWidget(splitter, 1)

        self.status_label = QLabel("就绪")
        layout.addWidget(self.status_label)

    def _build_header(self) -> QHBoxLayout:
        """面包屑 + 品种选择：批量录入是「保险」业务下的一项操作，不是孤立页面。

        「用哪张页面录」在这里选（默认取配方里的 product）——
        以前这个值只在 config/recipes/*.yaml 里写死，和主界面选的品种是两套配置源。
        """
        row = QHBoxLayout()
        back = QPushButton("‹ 返回主界面")
        back.setToolTip("回到主界面（品种库 + 属性面板）")
        back.clicked.connect(self.back_requested.emit)
        row.addWidget(back)
        crumb = QLabel("📋 批量录入")
        mark(crumb, "title")
        row.addWidget(crumb)

        row.addSpacing(12)
        row.addWidget(QLabel("品种："))
        self.product_combo = QComboBox()
        self.product_combo.setMinimumWidth(220)
        self.product_combo.setToolTip("要录入的目标页面配置（config/product_library/*.yaml）")
        self.product_combo.addItems(self._product_names())
        default_product = str(self.recipe.execute.get("product", "") or "")
        if default_product and self.product_combo.findText(default_product) < 0:
            self.product_combo.addItem(default_product)
        if default_product:
            self.product_combo.setCurrentText(default_product)
        self.product_combo.currentTextChanged.connect(self._update_product_hint)
        row.addWidget(self.product_combo)

        self.product_hint = QLabel("")
        mark(self.product_hint, "hint")
        row.addWidget(self.product_hint)
        self._update_product_hint(self.product_combo.currentText())

        row.addStretch()
        return row

    def _product_names(self) -> List[str]:
        """品种库里可选的品种名。"""
        try:
            from core.product_library import ProductLibrary

            return [p.name for p in ProductLibrary().list_products()]
        except Exception:  # noqa: BLE001 — 读不到库不该让页面打不开
            return []

    def _update_product_hint(self, name: str) -> None:
        """显示这个品种会打开哪个页面（选错品种 = 填错系统，必须看得见）。"""
        url = ""
        try:
            from core.product_library import ProductLibrary

            for product in ProductLibrary().list_products():
                if product.name == name:
                    url = product.target_url
                    break
        except Exception:  # noqa: BLE001
            url = ""
        self.product_hint.setText(f"→ {url}" if url else "")

    def _runner_recipe(self) -> Recipe:
        """按界面上的选择生成本次运行的配方（只在副本上改，不动全局配置）。"""
        import copy

        recipe = copy.deepcopy(self.recipe)
        chosen = self.product_combo.currentText().strip()
        if chosen:
            recipe.execute["product"] = chosen
        return recipe

    def _build_batches(self) -> QWidget:
        box = QGroupBox("批次")
        layout = QVBoxLayout(box)
        self.batch_list = QListWidget()
        self.batch_list.currentRowChanged.connect(self._on_batch_changed)
        layout.addWidget(self.batch_list, 1)

        self.import_btn = QPushButton("📥 导入 Excel / CSV")
        self.import_btn.clicked.connect(self.import_file)
        layout.addWidget(self.import_btn)

        self.run_btn = QPushButton("🚀 批量录入（已确认）")
        mark(self.run_btn, "primary")
        self.run_btn.clicked.connect(self.run_batch)
        layout.addWidget(self.run_btn)
        return box

    def _build_applicants(self) -> QWidget:
        box = QGroupBox("明细")
        layout = QVBoxLayout(box)

        self.summary_label = QLabel("—")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)

        self.table = QTableWidget(0, len(_TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(_TABLE_COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.currentCellChanged.connect(self._on_row_changed)
        layout.addWidget(self.table, 1)
        return box

    def _build_review(self) -> QWidget:
        box = QGroupBox("复核（J/K 上下条，Enter 通过，Backspace 打回）")
        layout = QVBoxLayout(box)

        self.review_title = QLabel("选择一条「待复核」记录开始")
        layout.addWidget(self.review_title)

        source_box = QGroupBox("源数据")
        source_layout = QVBoxLayout(source_box)
        self.source_view = QTextEdit()
        self.source_view.setReadOnly(True)
        self.source_view.setFixedHeight(120)
        source_layout.addWidget(self.source_view)
        layout.addWidget(source_box)

        shot_box = QGroupBox("录入后截图")
        shot_layout = QVBoxLayout(shot_box)
        self.screenshot_label = QLabel("（无截图）")
        self.screenshot_label.setAlignment(Qt.AlignCenter)
        self.screenshot_label.setMinimumHeight(180)
        self.screenshot_label.setStyleSheet("border: 1px solid #ccc;")
        shot_layout.addWidget(self.screenshot_label)
        layout.addWidget(shot_box, 1)

        check_box = QGroupBox("校验结果（异常高亮）")
        check_layout = QVBoxLayout(check_box)
        self.validation_view = QTextEdit()
        self.validation_view.setReadOnly(True)
        self.validation_view.setFixedHeight(140)
        check_layout.addWidget(self.validation_view)
        layout.addWidget(check_box)

        buttons = QHBoxLayout()
        self.prev_btn = QPushButton("↑ 上一条 (K)")
        self.prev_btn.clicked.connect(lambda: self.move_review(-1))
        self.next_btn = QPushButton("↓ 下一条 (J)")
        self.next_btn.clicked.connect(lambda: self.move_review(1))
        self.approve_btn = QPushButton("✅ 通过 (Enter)")
        self.approve_btn.clicked.connect(self.approve_current)
        self.reject_btn = QPushButton("↩ 打回 (Backspace)")
        mark(self.reject_btn, "danger")
        self.reject_btn.clicked.connect(self.reject_current)
        bulk_btn = QPushButton("⚡ 批量通过（无异常项）")
        bulk_btn.clicked.connect(self.bulk_approve)
        for button in (self.prev_btn, self.next_btn, self.approve_btn,
                       self.reject_btn, bulk_btn):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        return box

    # ------------------------------------------------------------------
    # 键盘：J/K/Enter/Backspace（设计文档 5.4）
    # ------------------------------------------------------------------
    def keyPressEvent(self, event):  # noqa: N802 — Qt 命名
        key = event.key()
        if key == Qt.Key_J:
            self.move_review(1)
        elif key == Qt.Key_K:
            self.move_review(-1)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            self.approve_current()
        elif key == Qt.Key_Backspace:
            self.reject_current()
        else:
            super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def current_batch_id(self) -> Optional[int]:
        item = self.batch_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def refresh_batches(self) -> None:
        self.batch_list.blockSignals(True)
        self.batch_list.clear()
        for batch in self.db.list_batches():
            tag = f"  [{batch.product}]" if batch.product else "  [未标注品种]"
            item = QListWidgetItem(
                f"#{batch.id} {batch.name}（{batch.total} 条）{tag}")
            item.setData(Qt.UserRole, batch.id)
            self.batch_list.addItem(item)
        self.batch_list.blockSignals(False)
        if self.batch_list.count():
            self.batch_list.setCurrentRow(0)
        else:
            self.table.setRowCount(0)
            self.summary_label.setText("尚无批次——先「导入 Excel / CSV」")

    def refresh_detail(self) -> None:
        batch_id = self.current_batch_id()
        if batch_id is None:
            return
        applicants = self.db.list_applicants(batch_id)
        self.table.setRowCount(len(applicants))
        for row, applicant in enumerate(applicants):
            values = (applicant.id, applicant.name, applicant.school,
                      applicant.grade, applicant.class_name, applicant.status,
                      f"第 {applicant.source_row} 行" if applicant.source_row else "",
                      applicant.fail_reason)
            for column, value in enumerate(values):
                cell = QTableWidgetItem("" if value is None else str(value))
                if column == 5:                     # 状态列着色
                    cell.setBackground(
                        Qt.white if applicant.status not in STATUS_COLORS
                        else Qt.white)
                    cell.setData(Qt.ForegroundRole, None)
                self.table.setItem(row, column, cell)
            color = STATUS_COLORS.get(applicant.status)
            if color:
                for column in range(len(_TABLE_COLUMNS)):
                    item = self.table.item(row, column)
                    if item is not None:
                        item.setBackground(Qt.GlobalColor.transparent)
                        item.setData(Qt.BackgroundRole, _qcolor(color))
        counts = self.reviewer.summary(batch_id)
        self.summary_label.setText(
            "　".join(f"{status} {counts[status]}" for status in ALL_STATUSES
                      if counts[status]) or "（无记录）")
        self.refresh_queue()

    def refresh_queue(self) -> None:
        batch_id = self.current_batch_id()
        self._items = self.reviewer.queue(batch_id) if batch_id else []
        self._index = 0
        self.show_review()

    # ------------------------------------------------------------------
    # 复核
    # ------------------------------------------------------------------
    def show_review(self) -> None:
        if not self._items:
            self.review_title.setText("没有待复核的记录")
            self.source_view.clear()
            self.validation_view.clear()
            self.screenshot_label.setText("（无截图）")
            self.screenshot_label.setPixmap(QPixmap())
            return
        self._index = max(0, min(self._index, len(self._items) - 1))
        item = self._items[self._index]
        applicant = item.applicant
        self.review_title.setText(
            f"第 {self._index + 1} / {len(self._items)} 条："
            f"{applicant.name}（id={applicant.id}）"
            + ("　⚠ 有异常项" if not item.clean else "　✅ 无异常项"))

        origin = [p for p in (str(applicant.source_sheet or ""),
                              f"第 {applicant.source_row} 行" if applicant.source_row else "")
                  if p]
        lines = [
            f"来源：{' · '.join(origin) if origin else '—（非表格来源）'}",
            f"姓名：{applicant.name}",
            f"身份证号：{applicant.display_id_card}",
            f"手机号：{applicant.phone}",
            f"学校：{applicant.school}（学生类别：{applicant.student_level}）",
            f"年级 / 班级：{applicant.grade} / {applicant.class_name}",
            f"保险期间：{applicant.period_start} ~ {applicant.period_end}",
            f"缴款人：{applicant.payer_name} {applicant.payer_phone}",
        ]
        if applicant.source_image:
            lines.append(f"原图：{applicant.source_image}")
        self.source_view.setPlainText("\n".join(lines))

        self._show_screenshot(applicant.screenshot)

        rows = []
        for result in item.validations:
            mark = "✓" if result.passed else "✗"
            color = "#2e7d32" if result.passed else "#c62828"
            rows.append(f'<span style="color:{color}">{mark} '
                        f'[{result.field}] {result.detail}</span>')
        self.validation_view.setHtml("<br>".join(rows) or "（无校验结果）")

    def _show_screenshot(self, path: str) -> None:
        if not path or not os.path.exists(path):
            self.screenshot_label.setPixmap(QPixmap())
            self.screenshot_label.setText("（无截图）" if not path else
                                          f"截图文件不存在：{path}")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.screenshot_label.setText(f"无法读取截图：{path}")
            return
        self.screenshot_label.setText("")
        self.screenshot_label.setPixmap(pixmap.scaled(
            self.screenshot_label.size(), Qt.KeepAspectRatio,
            Qt.SmoothTransformation))

    def move_review(self, delta: int) -> None:
        if not self._items:
            return
        self._index = (self._index + delta) % len(self._items)
        self.show_review()

    def approve_current(self) -> None:
        if not self._items:
            return
        item = self._items[self._index]
        self.reviewer.approve(item.applicant.id, operator="人工")
        self.refresh_detail()
        self.status_label.setText(f"已通过：{item.applicant.name}")

    def reject_current(self) -> None:
        if not self._items:
            return
        item = self._items[self._index]
        self.reviewer.reject(item.applicant.id, operator="人工")
        self.refresh_detail()
        self.status_label.setText(
            f"已打回：{item.applicant.name}（回到「已确认」，修正后可重录）")

    def bulk_approve(self) -> None:
        batch_id = self.current_batch_id()
        if batch_id is None:
            return
        count = self.reviewer.bulk_approve(batch_id, operator="人工")
        self.refresh_detail()
        self.status_label.setText(f"批量通过 {count} 条（仅无异常项）")

    # ------------------------------------------------------------------
    # 导入 / 录入
    # ------------------------------------------------------------------
    def import_file(self, path: str = "") -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(
                self, "选择台账文件", "",
                "表格 (*.xlsx *.xlsm *.csv);;所有文件 (*)")
        if not path:
            return
        from batch.importer import Importer

        # 用**批量页上选的品种**导入（不是配方默认的那个），并先做一次预检：
        # 台账表头对不上当前品种就直接问，别等跑完一百条才发现全错
        recipe = self._runner_recipe()
        importer = Importer(self.db, recipe=recipe)
        ok, detail = importer.check_table(path)
        if not ok:
            from .dialogs import ask

            if not ask(self, "台账与当前品种可能对不上",
                       detail + "\n\n仍然导入吗？",
                       yes="仍然导入", no="取消"):
                return
        try:
            report = importer.import_file(path, sheet="")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(e))
            return
        finally:
            importer.db.close()

        QMessageBox.information(self, "导入完成", report.summary)
        self.refresh_batches()
        self.status_label.setText(
            f"导入完成：通过 {report.passed}，数据异常 {report.data_error}")

    def run_batch(self) -> None:
        batch_id = self.current_batch_id()
        if batch_id is None:
            QMessageBox.information(self, "提示", "先导入一个批次")
            return
        if self._worker is not None and self._worker.isRunning():
            return

        # 批次是为哪个品种导入的？和当前选的不一致就先问一句
        batch = self.db.get_batch(batch_id)
        chosen = self.product_combo.currentText()
        if batch is not None and batch.product and batch.product != chosen:
            from .dialogs import ask

            if not ask(self, "品种不一致",
                       f"批次「{batch.name}」是为「{batch.product}」导入的，"
                       f"当前选的是「{chosen}」。\n\n"
                       "继续会用当前品种的页面填这批数据，确定吗？",
                       yes="继续", no="取消"):
                return
        if QMessageBox.question(
                self, "批量录入",
                f"将用「{self.product_combo.currentText()}」逐条打开页面填写"
                "（**不会自动提交**），期间请勿操作浏览器。\n继续吗？") != QMessageBox.Yes:
            return

        from batch.runner import BatchRunner

        runner = BatchRunner(self.db, recipe=self._runner_recipe())
        worker = _RunWorker(runner, batch_id, parent=self)

        # 逐条确认：配方里 confirm_policy: per_record 时，每填完一条弹窗问通过/打回
        policy = str(self.recipe.execute.get("confirm_policy", "batch") or "batch").lower()
        if policy == "per_record":
            runner.on_confirm = worker.make_confirmer()
            worker.confirm_requested.connect(self._on_confirm_requested)

        self.run_btn.setEnabled(False)
        self.run_btn.setText("⏳ 录入中……")
        self._worker = worker
        self._worker.finished_report.connect(self._on_run_finished)
        self._worker.failed.connect(self._on_run_failed)
        self._worker.start()

    def _on_confirm_requested(self, applicant, fill) -> None:
        """逐条确认弹窗（主线程）：页面上已贴好批注，这里给结论。"""
        problems = "\n".join(fill.problems) or "（无异常）"
        text = (f"{applicant.name}：已填 {fill.filled_fields} 个字段"
                f"（浏览器里标注了期望值，不一致的是红框）。\n\n"
                f"异常项：\n{problems}\n\n通过这条吗？")
        answer = QMessageBox.question(self, "逐条确认", text,
                                      QMessageBox.Yes | QMessageBox.No,
                                      QMessageBox.Yes)
        self._worker.answer(answer == QMessageBox.Yes)

    def _on_run_finished(self, report) -> None:
        self.run_btn.setEnabled(True)
        self.run_btn.setText("🚀 批量录入（已确认）")
        self.refresh_detail()
        text = report.summary
        if getattr(report, "html_report", ""):
            text += f"\n\n📄 对比报告（期望/实际/状态 + 截图）：\n{report.html_report}"
        QMessageBox.information(self, "批量录入完成", text)

    def _on_run_failed(self, error: str) -> None:
        self.run_btn.setEnabled(True)
        self.run_btn.setText("🚀 批量录入（已确认）")
        QMessageBox.warning(self, "批量录入失败", error)

    # ------------------------------------------------------------------
    # 槽
    # ------------------------------------------------------------------
    def _on_batch_changed(self, _row: int) -> None:
        self.refresh_detail()

    def _on_row_changed(self, row: int, _column: int, *_args) -> None:
        """点明细表 → 跳到对应的复核条目（若是待复核）。"""
        if batch_id := self.current_batch_id():
            applicant_id = None
            if 0 <= row < self.table.rowCount():
                cell = self.table.item(row, 0)
                if cell is not None and cell.text().isdigit():
                    applicant_id = int(cell.text())
            if applicant_id is None:
                return
            for index, item in enumerate(self._items):
                if item.applicant.id == applicant_id:
                    self._index = index
                    self.show_review()
                    break
            _ = batch_id


def _qcolor(hex_color: str):
    from PyQt5.QtGui import QColor

    return QColor(hex_color)


__all__ = ["BatchPage"]
