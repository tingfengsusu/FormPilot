"""属性管理页面 — 程序内部页面（HStackedWidget 的一页），创建/编辑属性对象。

设计要点（来自 UI 设计评审）：
  - 这是主窗口内部切换的页面，不是弹窗
  - 操作按钮在表格上方：新增 / 删除 / 恢复默认 / 放弃更改 / 保存 / 返回
  - 直接在表格内联编辑（来源、类型为下拉；必填为复选框），不再有独立表单
  - 内置属性行：属性名只读（防止改名产生重复），可修改其它字段形成「覆盖」
  - 「选项」列仅在下拉选择类型时可编辑，其余类型显示 ——
  - 保存 = 把表格内容与内置默认逐行比对，只把「自定义 / 已覆盖」的行
    写入 config/property_library.yaml
  - 未保存修改时返回会询问：保存并返回 / 放弃修改 / 取消

同时提供 PropertyManagerDialog（薄封装），供品种创建向导等模态场景复用。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.property_def import (
    PropertyDef, PropertyDefLibrary, SOURCES, TYPE_LABELS, TYPE_SELECT,
    builtin_defs,
)
from .theme import mark

_HEADERS = ["属性名", "归属来源", "类型", "选项", "默认值", "必填", "描述"]
_COL_NAME, _COL_SOURCE, _COL_TYPE, _COL_OPTIONS, _COL_DEFAULT, _COL_REQUIRED, _COL_DESC = range(7)

#: 用户层行的底色（自定义 / 已覆盖内置）
_USER_ROW_BG = QColor("#fff8e1")

_OPTIONS_PLACEHOLDER = "——"


class PropertyManagerWidget(QWidget):
    """属性管理页（可嵌入主窗口，也可被对话框复用）。"""

    #: 用户点击「返回/关闭」
    back_requested = pyqtSignal()

    def __init__(self, library: Optional[PropertyDefLibrary] = None,
                 back_text: str = "⬅ 返回主界面", parent=None):
        super().__init__(parent)
        self.library = library or PropertyDefLibrary()
        self._dirty = False
        self._loading = False  # 程序化填充表格时抑制 dirty 标记

        layout = QVBoxLayout(self)

        # ---- 工具条（按钮在表格上方）----
        toolbar = QHBoxLayout()
        self.title_label = QLabel("🏷️ 属性管理")
        mark(self.title_label, "title")
        toolbar.addWidget(self.title_label)
        toolbar.addSpacing(12)

        self.add_btn = QPushButton("➕ 新增属性")
        self.add_btn.clicked.connect(self.add_row)
        self.delete_btn = QPushButton("🗑 删除")
        self.delete_btn.clicked.connect(self.delete_selected)
        self.revert_btn = QPushButton("↺ 恢复默认")
        self.revert_btn.setToolTip("把选中的内置属性恢复为内置默认定义（撤销覆盖）")
        self.revert_btn.clicked.connect(self.revert_selected)
        self.discard_btn = QPushButton("↶ 放弃更改")
        self.discard_btn.clicked.connect(self.discard_changes)
        self.save_btn = QPushButton("💾 保存")
        mark(self.save_btn, "primary")
        self.save_btn.clicked.connect(self.save)
        toolbar.addWidget(self.add_btn)
        toolbar.addWidget(self.delete_btn)
        toolbar.addWidget(self.revert_btn)
        toolbar.addWidget(self.discard_btn)
        toolbar.addStretch()
        self.back_btn = QPushButton(back_text)
        self.back_btn.clicked.connect(self._on_back)
        toolbar.addWidget(self.back_btn)
        layout.addLayout(toolbar)

        # ---- 表格（内联编辑）----
        self.table = QTableWidget(0, len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._on_item_changed)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setSectionResizeMode(_COL_OPTIONS, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_DESC, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

        # ---- 状态区 ----
        self.status_label = QLabel("")
        mark(self.status_label, "hint")
        layout.addWidget(self.status_label)

        self._hint = QLabel(
            "提示：黄色底纹行 = 自定义或已覆盖内置的属性；内置属性行名不可改，"
            "「恢复默认」可撤销覆盖；「选项」列仅下拉选择类型可编辑。")
        self._hint.setWordWrap(True)
        mark(self._hint, "hint")
        layout.addWidget(self._hint)

        self.refresh_table()

    # ------------------------------------------------------------------
    # 表格加载 / 保存
    # ------------------------------------------------------------------
    def refresh_table(self, select_name: Optional[str] = None) -> None:
        """从属性库重新加载表格。"""
        self._loading = True
        try:
            self.table.setRowCount(0)
            builtin_names = {d.name for d in builtin_defs()}
            user_names = self.library.user_def_names()

            for d in self.library.all_defs():
                self._append_row(d, is_builtin=d.name in builtin_names,
                                 is_user=d.name in user_names)
            if select_name:
                for row in range(self.table.rowCount()):
                    if self.table.item(row, _COL_NAME).text() == select_name:
                        self.table.selectRow(row)
                        break
        finally:
            self._loading = False
        self._dirty = False
        self._set_status(f"共 {self.table.rowCount()} 个属性"
                         f"（自定义/覆盖 {len(self.library.user_def_names())} 个）")

    def _append_row(self, d: PropertyDef, is_builtin: bool, is_user: bool) -> int:
        """按属性对象追加一行（内置行的属性名只读）。"""
        row = self.table.rowCount()
        self.table.insertRow(row)

        # 属性名
        name_item = QTableWidgetItem(d.name)
        if is_builtin:
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setToolTip("内置属性：属性名不可修改")
        if is_user:
            name_item.setBackground(_USER_ROW_BG)
            name_item.setToolTip("自定义或已覆盖内置的属性")
        self.table.setItem(row, _COL_NAME, name_item)

        # 归属来源（下拉）
        source_combo = QComboBox()
        source_combo.addItems(SOURCES)
        source_combo.setCurrentText(d.source if d.source in SOURCES else "自定义")
        source_combo.currentIndexChanged.connect(self._mark_dirty)
        self.table.setCellWidget(row, _COL_SOURCE, source_combo)

        # 类型（下拉）
        type_combo = QComboBox()
        for tid, label in TYPE_LABELS.items():
            type_combo.addItem(label, tid)
        idx = type_combo.findData(d.field_type)
        type_combo.setCurrentIndex(idx if idx >= 0 else 0)
        type_combo.currentIndexChanged.connect(
            lambda _i, r=row: self._on_type_changed(r))
        self.table.setCellWidget(row, _COL_TYPE, type_combo)

        # 选项
        options_item = QTableWidgetItem()
        options_item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setItem(row, _COL_OPTIONS, options_item)
        self._apply_options_state(row, d.field_type, d.options)

        # 默认值 / 描述
        self.table.setItem(row, _COL_DEFAULT, QTableWidgetItem(d.default_value))
        self.table.setItem(row, _COL_DESC, QTableWidgetItem(d.description))

        # 必填（复选框）
        req_item = QTableWidgetItem()
        req_item.setFlags((req_item.flags() | Qt.ItemIsUserCheckable)
                          & ~Qt.ItemIsEditable)
        req_item.setCheckState(Qt.Checked if d.required else Qt.Unchecked)
        req_item.setTextAlignment(Qt.AlignCenter)
        self.table.setItem(row, _COL_REQUIRED, req_item)

        return row

    def _apply_options_state(self, row: int, field_type: str,
                             options: List[str]) -> None:
        """按类型设置「选项」列：仅下拉选择类型可编辑。"""
        item = self.table.item(row, _COL_OPTIONS)
        if item is None:
            return
        if field_type == TYPE_SELECT:
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            item.setForeground(QColor("#222"))
            item.setText("，".join(options))
        else:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            item.setForeground(QColor("#aaa"))
            item.setText(_OPTIONS_PLACEHOLDER)

    def _on_type_changed(self, row: int) -> None:
        if self._loading:
            return
        combo = self.table.cellWidget(row, _COL_TYPE)
        field_type = combo.currentData()
        item = self.table.item(row, _COL_OPTIONS)
        current = item.text() if item else ""
        if field_type == TYPE_SELECT:
            if current in ("", _OPTIONS_PLACEHOLDER):
                self._loading = True
                item.setText("")
                self._loading = False
            self._apply_options_state(row, field_type, [])
            self.table.item(row, _COL_OPTIONS).setText(current if current != _OPTIONS_PLACEHOLDER else "")
        else:
            self._apply_options_state(row, field_type, [])
        self._mark_dirty()

    # ------------------------------------------------------------------
    # 行操作
    # ------------------------------------------------------------------
    def add_row(self) -> None:
        """表尾插入空白行并进入编辑。"""
        blank = PropertyDef(name="", source="自定义")
        row = self._append_row(blank, is_builtin=False, is_user=False)
        # 新行属性名可编辑
        self.table.item(row, _COL_NAME).setFlags(
            self.table.item(row, _COL_NAME).flags() | Qt.ItemIsEditable)
        self.table.scrollToBottom()
        self.table.setCurrentCell(row, _COL_NAME)
        self.table.editItem(self.table.item(row, _COL_NAME))
        self._mark_dirty()

    def _selected_row(self) -> int:
        return self.table.currentRow()

    def delete_selected(self) -> None:
        """删除选中行：自定义属性直接删；内置属性只能「恢复默认」。"""
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格中选中一行")
            return
        name = self.table.item(row, _COL_NAME).text().strip()
        builtin_names = {d.name for d in builtin_defs()}
        user_names = self.library.user_def_names()

        if not name:
            # 尚未保存的新行：直接从表格移除
            self.table.removeRow(row)
            self._mark_dirty()
            return

        if name in builtin_names:
            QMessageBox.information(
                self, "无法删除",
                f"「{name}」是内置属性，不能删除。"
                f"\n如需撤销对它的修改，请使用「↺ 恢复默认」。")
            return

        if name in user_names:
            confirm = QMessageBox.question(
                self, "删除属性", f"确定删除自定义属性「{name}」吗？")
            if confirm != QMessageBox.Yes:
                return
            self.library.remove(name)
            self.refresh_table()
            self._set_status(f"已删除属性「{name}」")
            return

        # 新增未保存的行（自定义名字还没入库）
        self.table.removeRow(row)
        self._mark_dirty()

    def revert_selected(self) -> None:
        """把选中的内置属性恢复为内置默认（撤销用户覆盖）。"""
        row = self._selected_row()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格中选中一行")
            return
        name = self.table.item(row, _COL_NAME).text().strip()
        builtin = {d.name: d for d in builtin_defs()}.get(name)
        if builtin is None:
            QMessageBox.information(
                self, "提示",
                f"「{name}」不是内置属性，没有可恢复的默认定义。")
            return
        if name not in self.library.user_def_names():
            QMessageBox.information(self, "提示", f"「{name}」已是内置默认定义。")
            return
        self.library.remove(name)
        self.refresh_table(select_name=name)
        self._set_status(f"已将「{name}」恢复为内置默认")

    def discard_changes(self) -> None:
        """放弃所有未保存的修改，重新载入。"""
        if not self._dirty:
            self._set_status("没有未保存的修改")
            return
        confirm = QMessageBox.question(self, "放弃更改", "确定放弃未保存的修改吗？")
        if confirm != QMessageBox.Yes:
            return
        self.refresh_table()
        self._set_status("已放弃未保存的修改")

    # ------------------------------------------------------------------
    # 保存
    # ------------------------------------------------------------------
    def collect_defs(self) -> List[PropertyDef]:
        """从表格收集全部属性对象（含校验，失败抛 ValueError）。"""
        defs: List[PropertyDef] = []
        seen = set()
        for row in range(self.table.rowCount()):
            name = self.table.item(row, _COL_NAME).text().strip()
            source = self.table.cellWidget(row, _COL_SOURCE).currentText()
            field_type = self.table.cellWidget(row, _COL_TYPE).currentData()
            options_text = self.table.item(row, _COL_OPTIONS).text()
            options = [] if options_text == _OPTIONS_PLACEHOLDER else [
                s.strip() for s in
                options_text.replace("，", ",").split(",") if s.strip()]

            if not name:
                raise ValueError(f"第 {row + 1} 行：属性名不能为空")
            if name in seen:
                raise ValueError(f"第 {row + 1} 行：属性名「{name}」重复")
            if field_type == TYPE_SELECT and not options:
                raise ValueError(f"第 {row + 1} 行：「{name}」为下拉选择，必须填写选项")
            seen.add(name)

            defs.append(PropertyDef(
                name=name,
                source=source,
                field_type=field_type,
                options=options,
                default_value=self.table.item(row, _COL_DEFAULT).text().strip(),
                required=self.table.item(row, _COL_REQUIRED).checkState() == Qt.Checked,
                description=self.table.item(row, _COL_DESC).text().strip(),
            ))
        return defs

    def save(self) -> None:
        """保存：只把与内置默认不同的行写入用户定义层。"""
        try:
            defs = self.collect_defs()
        except ValueError as e:
            QMessageBox.warning(self, "保存失败", str(e))
            return

        builtin_map = {d.name: d for d in builtin_defs()}
        user_defs = [d for d in defs
                     if not self._equals_builtin(d, builtin_map.get(d.name))]
        try:
            self.library.save_all(user_defs)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return

        self.refresh_table()
        self._set_status(
            f"已保存：自定义/覆盖 {len(user_defs)} 个属性，"
            f"其余 {len(defs) - len(user_defs)} 个为内置默认")

    @staticmethod
    def _equals_builtin(d: PropertyDef, builtin: Optional[PropertyDef]) -> bool:
        """行内容是否与内置默认完全一致（一致则无需写入用户层）。"""
        if builtin is None:
            return False
        return (d.source == builtin.source
                and d.field_type == builtin.field_type
                and d.options == builtin.options
                and d.default_value == builtin.default_value
                and d.required == builtin.required
                and d.description == builtin.description)

    # ------------------------------------------------------------------
    # 脏标记 / 返回
    # ------------------------------------------------------------------
    def _mark_dirty(self, *_args) -> None:
        if self._loading:
            return
        if not self._dirty:
            self._dirty = True
            self._set_status("有未保存的修改")

    def _on_item_changed(self, _item) -> None:
        self._mark_dirty()

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_back(self) -> None:
        """返回：有未保存修改时询问。"""
        if self._dirty:
            box = QMessageBox(self)
            box.setWindowTitle("未保存的修改")
            box.setText("属性库有未保存的修改，是否保存？")
            save_btn = box.addButton("保存并返回", QMessageBox.AcceptRole)
            drop_btn = box.addButton("放弃修改", QMessageBox.DestructiveRole)
            box.addButton("取消", QMessageBox.RejectRole)
            box.exec_()
            if box.clickedButton() is save_btn:
                self.save()
                if self._dirty:   # 保存失败（校验不通过）则留在本页
                    return
            elif box.clickedButton() is drop_btn:
                pass
            else:
                return
        self.back_requested.emit()


class PropertyManagerDialog(QDialog):
    """属性管理对话框（薄封装，供品种创建向导等模态场景使用）。"""

    def __init__(self, parent=None, library: Optional[PropertyDefLibrary] = None):
        super().__init__(parent)
        self.setWindowTitle("属性管理")
        self.resize(900, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.widget = PropertyManagerWidget(library=library, back_text="关闭")
        self.widget.back_requested.connect(self.accept)
        layout.addWidget(self.widget)
