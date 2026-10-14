"""「从样例表对齐字段」对话框：把 Excel 的列和页面上扫到的字段摆在一起让人确认。

界面（左 → 右）：

    用 | Excel 列（样例值）        | 页面字段（下拉）      | 属性名      | 建议理由
    ☑  | 被保人姓名 张小明          | 请录入姓名 ▾          | 学生姓名    | 页面标签命中
    ☐  | 缴费状态  已缴            | — 不填 —              | —          | 列名不在字典里
    ☐  | （页面有但没人绑）          | 年级 ▾                | 就读年级    | 页面字段未被认领

确认后返回一份"属性 → locator（+ 转换）"清单，由品种编辑页写进属性表。
**样例值只在本机显示**（身份证/手机在界面上打码），不外发、不落盘。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QHeaderView, QLabel,
                             QLineEdit, QTableWidget, QTableWidgetItem,
                             QVBoxLayout)

from engine.field_align import (ColumnSuggestion, SHAPE_ID_CARD, SHAPE_PHONE,
                                suggest_alignments, unmatched_page_fields)

from .theme import mark

_NOT_SET = "—— 不填 ——"


def mask_sample(value: str, shape: str) -> str:
    """样例值打码显示（身份证保头尾、手机保前 3 后 4）。"""
    text = str(value or "")
    if shape == SHAPE_ID_CARD and len(text) >= 10:
        return text[:6] + "*" * (len(text) - 10) + text[-4:]
    if shape == SHAPE_PHONE and len(text) == 11:
        return text[:3] + "****" + text[-4:]
    return text


class AlignDialog(QDialog):
    """一屏确认"哪一列进哪个字段"。"""

    def __init__(self, columns: Sequence[str], sample_rows: Sequence[Dict[str, Any]],
                 page_fields: Sequence[Dict[str, Any]],
                 dictionary: Dict[str, Sequence[str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("从样例表对齐字段")
        self.resize(980, 560)
        self._page_fields = list(page_fields)

        suggestions = suggest_alignments(list(columns), list(sample_rows),
                                        self._page_fields, dictionary)
        # 页面有、但没被任何列认领的控件，也列出来（可能来自其它数据源）
        self._extra_fields = unmatched_page_fields(suggestions, self._page_fields)

        layout = QVBoxLayout(self)
        hint = QLabel("左边是样例表的列，右边是扫到的页面字段。勾选并确认后写入属性表；"
                      "样例值只在本机显示（敏感值已打码），不会外发。")
        hint.setWordWrap(True)
        layout.addWidget(mark(hint, "hint"))

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["用", "Excel 列（样例值）", "页面字段", "属性名", "建议理由"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        for suggestion in suggestions:
            self._append_from_suggestion(suggestion)
        for label in self._extra_fields:
            self._append_unclaimed(label)

        self._summary = QLabel("")
        mark(self._summary, "hint")
        layout.addWidget(self._summary)
        self._refresh_summary(suggestions)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("写入属性表")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------
    def _append_from_suggestion(self, suggestion: ColumnSuggestion) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        check = QCheckBox()
        check.setChecked(suggestion.used)
        self.table.setCellWidget(row, 0, check)

        sample = mask_sample(suggestion.sample, suggestion.shape)
        cell = QTableWidgetItem(f"{suggestion.column}　{sample}".strip())
        cell.setData(Qt.UserRole, suggestion.column)
        self.table.setItem(row, 1, cell)

        combo = self._field_combo(suggestion.locator)
        self.table.setCellWidget(row, 2, combo)

        name = QLineEdit(suggestion.column)          # 属性名默认=列名，可改
        self.table.setCellWidget(row, 3, name)

        reasons = "；".join(suggestion.reasons) or "—"
        if suggestion.transform:
            reasons += f"｜转换：{suggestion.transform}"
        if suggestion.ambiguous:
            reasons = f"⚠ 可能属于 {'/'.join(suggestion.candidates)}；" + reasons
        reason_item = QTableWidgetItem(reasons)
        reason_item.setToolTip(reasons)
        self.table.setItem(row, 4, reason_item)
        if not suggestion.used:
            for col in (1, 4):
                self.table.item(row, col).setForeground(Qt.gray)

    def _append_unclaimed(self, label: str) -> None:
        """页面有但没被认领的控件：也给一行，让用户有机会绑上。"""
        row = self.table.rowCount()
        self.table.insertRow(row)
        check = QCheckBox()
        check.setChecked(False)
        self.table.setCellWidget(row, 0, check)
        item = QTableWidgetItem("（页面字段，样例表里没有对应的列）")
        item.setForeground(Qt.gray)
        self.table.setItem(row, 1, item)
        self.table.setCellWidget(row, 2, self._field_combo("", label=label))
        self.table.setCellWidget(row, 3, QLineEdit(label))
        reason = QTableWidgetItem("页面有这个字段，但样例表里没有能对上的列")
        reason.setForeground(Qt.gray)
        self.table.setItem(row, 4, reason)

    def _field_combo(self, current_locator: str, label: str = "") -> QComboBox:
        combo = QComboBox()
        combo.addItem(_NOT_SET, "")
        for item in self._page_fields:
            locator = str(item.get("locator") or "")
            if not locator:
                continue
            text = (item.get("label") or item.get("placeholder") or locator)
            combo.addItem(str(text)[:46], locator)
        index = combo.findData(current_locator) if current_locator else -1
        if index < 0 and label:
            for i in range(combo.count()):
                if label and label[:8] in combo.itemText(i):
                    index = i
                    break
        combo.setCurrentIndex(index if index >= 0 else 0)
        return combo

    def _refresh_summary(self, suggestions: Sequence[ColumnSuggestion]) -> None:
        unused = [s.column for s in suggestions if not s.used]
        parts = []
        if unused:
            parts.append("没对上的列：" + "、".join(unused[:6])
                         + ("…" if len(unused) > 6 else ""))
        if self._extra_fields:
            parts.append("页面上没人绑的字段：" + "、".join(self._extra_fields[:6]))
        self._summary.setText("｜".join(parts) if parts else "列与页面字段已全部对上")

    # ------------------------------------------------------------------
    def result_rows(self) -> List[Dict[str, Any]]:
        """确认后的结果：``[{property, locator, transform}]``（只含勾选且有 locator 的）。"""
        out: List[Dict[str, Any]] = []
        for row in range(self.table.rowCount()):
            check = self.table.cellWidget(row, 0)
            combo = self.table.cellWidget(row, 2)
            name = self.table.cellWidget(row, 3)
            if not isinstance(check, QCheckBox) or not check.isChecked():
                continue
            locator = combo.currentData() if isinstance(combo, QComboBox) else ""
            prop = name.text().strip() if isinstance(name, QLineEdit) else ""
            if not prop or not locator:
                continue
            out.append({"property": prop, "locator": str(locator)})
        return out


__all__ = ["AlignDialog", "mask_sample"]
