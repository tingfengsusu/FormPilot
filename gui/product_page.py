"""品种编辑页：一屏编完一个品种（创建与编辑共用）。取代原来的 5 步向导。

为什么是单页：

- 品种是一份配置 —— 名称/描述、目标页面、以及"这张页面要填什么、怎么填"，
  本来就该在一屏里看全、就地改。向导把「确认映射 + 回读校验」放在第 5 步，
  前面任何一步不顺（比如扫描失败）就到不了那儿
- 品种保存后原来**无法再编辑**（只能手改 yaml 或重走向导），这里一并补上
- 「另存为」天然支持"基础品种 + 机构差异"的复制改造

布局（PLM 表单式）：头部（名称/描述/URL/浏览器环境 + 扫描）→ 属性表 → 底部动作。
"""
from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog,
                             QDialogButtonBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QListWidget, QListWidgetItem, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from core.product import FieldMapping, Product
from core.product_library import ProductLibrary
from core.property_def import PropertyDefLibrary

from .theme import mark

#: 字段填写策略（与 core/product.FieldMapping.fill_mode 一致）
FILL_MODES = ("plain", "select", "autocomplete", "click", "date", "verify_only")

_FILL_MODE_HINT = {
    "plain": "plain（直接填）",
    "select": "select（下拉）",
    "autocomplete": "autocomplete（联想框）",
    "click": "click（点击型）",
    "date": "date（日期选择器）",
    "verify_only": "verify_only（只回读）",
}


class _PropertyPicker(QDialog):
    """从属性库多选属性（勾选后返回属性名列表）。"""

    def __init__(self, prop_def_library: PropertyDefLibrary,
                 preselected: Optional[List[str]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从属性库添加属性")
        self.resize(420, 460)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("勾选要加进这个品种的属性（属性库：内置 + 自定义）"))

        self.list_widget = QListWidget()
        checked = set(preselected or [])
        for d in prop_def_library.all_defs():
            item = QListWidgetItem(f"{d.name}　[{d.source}]")
            item.setData(Qt.UserRole, d.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if d.name in checked else Qt.Unchecked)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected(self) -> List[str]:
        return [self.list_widget.item(i).data(Qt.UserRole)
                for i in range(self.list_widget.count())
                if self.list_widget.item(i).checkState() == Qt.Checked]


class ProductPage(QWidget):
    """品种编辑页（创建 / 编辑共用）。"""

    saved = pyqtSignal(str)          # 参数：保存后的品种名
    back_requested = pyqtSignal()

    def __init__(self, library: Optional[ProductLibrary] = None,
                 prop_def_library: Optional[PropertyDefLibrary] = None,
                 parent=None):
        super().__init__(parent)
        self.library = library or ProductLibrary()
        self.prop_def_library = prop_def_library or PropertyDefLibrary()
        self._original_name = ""
        #: 表格里没暴露的字段（编辑时原样保留，避免保存后丢配置）
        self._extra: dict = {}

        outer = QVBoxLayout(self)
        outer.addLayout(self._build_header())
        outer.addWidget(self._build_table(), 1)
        outer.addLayout(self._build_footer())

    # ------------------------------------------------------------------
    # 界面
    # ------------------------------------------------------------------
    def _build_header(self) -> QVBoxLayout:
        box = QVBoxLayout()
        title_row = QHBoxLayout()
        self.title_label = QLabel("品种编辑")
        mark(self.title_label, "title")
        title_row.addWidget(self.title_label)
        hint = QLabel("新建或修改一个品种：这张页面要填什么、怎么填，一屏编完")
        mark(hint, "hint")
        title_row.addWidget(hint)
        title_row.addStretch()
        box.addLayout(title_row)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.addWidget(QLabel("名称"), 0, 0)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("如：太保洛阳学平险_基础")
        grid.addWidget(self.name_edit, 0, 1)
        grid.addWidget(QLabel("描述"), 0, 2)
        self.desc_edit = QLineEdit()
        grid.addWidget(self.desc_edit, 0, 3, 1, 3)

        grid.addWidget(QLabel("目标 URL"), 1, 0)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://…（微信 H5 可先扫二维码取真实链接）")
        grid.addWidget(self.url_edit, 1, 1, 1, 3)
        self.scan_btn = QPushButton("🔍 扫描页面字段")
        self.scan_btn.setToolTip("打开目标页面，识别各字段的 locator 并回填到下表")
        self.scan_btn.clicked.connect(self.scan_fields)
        grid.addWidget(self.scan_btn, 1, 4)
        qr_btn = QPushButton("📷 二维码导入 URL")
        qr_btn.setToolTip("微信 H5：直输网址会被拒，从二维码图片解出真实链接")
        qr_btn.clicked.connect(self.import_url_from_qr)
        grid.addWidget(qr_btn, 1, 5)

        env_row = QHBoxLayout()
        env_row.addWidget(QLabel("浏览器环境："))
        self.mobile_check = QCheckBox("移动端（UA + touch + 视口）")
        self.wechat_check = QCheckBox("微信内置浏览器 UA")
        env_row.addWidget(self.mobile_check)
        env_row.addWidget(self.wechat_check)
        env_row.addWidget(QLabel("Referer"))
        self.referer_edit = QLineEdit()
        self.referer_edit.setPlaceholderText("留空则取目标 URL 的 origin")
        env_row.addWidget(self.referer_edit, 1)
        grid.addLayout(env_row, 2, 0, 1, 6)

        box.addLayout(grid)
        return box

    def _build_table(self) -> QGroupBox:
        box = QGroupBox("属性（这张页面要填什么）")
        layout = QVBoxLayout(box)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["属性", "locator", "必填", "回读校验", "填写策略"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for col in (2, 3, 4):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        add_btn = QPushButton("＋ 从属性库添加")
        mark(add_btn, "primary")
        add_btn.clicked.connect(self.add_properties)
        row.addWidget(add_btn)
        up_btn = QPushButton("↑ 上移")
        up_btn.clicked.connect(lambda: self._move_row(-1))
        row.addWidget(up_btn)
        down_btn = QPushButton("↓ 下移")
        down_btn.clicked.connect(lambda: self._move_row(1))
        row.addWidget(down_btn)
        del_btn = QPushButton("🗑 删除行")
        del_btn.clicked.connect(self.delete_rows)
        row.addWidget(del_btn)
        row.addStretch()
        hint = QLabel("回读校验 = 填完读回比对；识别/级联/日期类字段建议保持勾选")
        mark(hint, "hint")
        row.addWidget(hint)
        layout.addLayout(row)
        return box

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.save_btn = QPushButton("💾 保存")
        mark(self.save_btn, "primary")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.save_btn)

        self.save_as_btn = QPushButton("另存为…")
        self.save_as_btn.setToolTip("改个名字保存（基础品种 + 机构差异就靠它）")
        self.save_as_btn.clicked.connect(self.save_as)
        row.addWidget(self.save_as_btn)

        self.delete_btn = QPushButton("🗑 删除品种")
        mark(self.delete_btn, "danger")
        self.delete_btn.clicked.connect(self.delete_product)
        row.addWidget(self.delete_btn)

        row.addStretch()
        self.status_label = QLabel("")
        mark(self.status_label, "hint")
        row.addWidget(self.status_label)

        back = QPushButton("‹ 返回主界面")
        back.clicked.connect(self.back_requested.emit)
        row.addWidget(back)
        return row

    # ------------------------------------------------------------------
    # 数据：加载 / 收集
    # ------------------------------------------------------------------
    def load_product(self, product: Optional[Product] = None) -> None:
        """载入一个品种到表单；``None`` = 新建（清空）。"""
        self.table.setRowCount(0)
        self._extra = {}
        if product is None:
            self._original_name = ""
            self.name_edit.clear()
            self.desc_edit.clear()
            self.url_edit.clear()
            self.referer_edit.clear()
            self.mobile_check.setChecked(False)
            self.wechat_check.setChecked(False)
            self._set_title("")          # 别留着上一个品种的标题（自测截图抓到的 bug）
            self.status_label.setText("新建品种：填名称 → 加属性 → 扫描 locator")
            return

        self._original_name = product.name
        self._extra = {
            "submit_button": product.submit_button,
            "verify_delay_ms": product.verify_delay_ms,
            "required_sources": list(product.required_sources),
        }
        self.name_edit.setText(product.name)
        self.desc_edit.setText(product.description)
        self.url_edit.setText(product.target_url)
        context = dict(product.context or {})
        self.mobile_check.setChecked(bool(context.get("mobile")))
        self.wechat_check.setChecked(bool(context.get("wechat")))
        self.referer_edit.setText(str(context.get("referer") or ""))
        for mapping in product.field_mapping:
            self._append_row(mapping.property, mapping.locator, mapping.required,
                             mapping.verify_after_fill, mapping.fill_mode)
        self.status_label.setText(f"已载入：{product.name}")
        self._set_title(product.name)

    def collect_product(self) -> Product:
        """把表单读成 Product（未填 locator 的属性保留 locator 为空）。"""
        mappings: List[FieldMapping] = []
        for row in range(self.table.rowCount()):
            prop = self._cell_text(row, 0)
            if not prop:
                continue
            mode = self._row_mode(row)
            mappings.append(FieldMapping(
                property=prop,
                locator=self._cell_text(row, 1),
                required=self._cell_checked(row, 2),
                verify_after_fill=self._cell_checked(row, 3),
                fill_mode=mode,
            ))
        context = self._collect_context()
        return Product(
            name=self.name_edit.text().strip(),
            description=self.desc_edit.text().strip(),
            target_url=self.url_edit.text().strip(),
            field_mapping=mappings,
            submit_button=self._extra.get("submit_button", 'button[type="submit"]'),
            verify_delay_ms=int(self._extra.get("verify_delay_ms", 500)),
            required_sources=list(self._extra.get("required_sources", [])),
            context=context,
        )

    # ------------------------------------------------------------------
    # 动作
    # ------------------------------------------------------------------
    def add_properties(self) -> None:
        """从属性库多选添加属性行（已在本表的属性保持勾选）。"""
        picker = _PropertyPicker(self.prop_def_library, self._property_names(), self)
        if picker.exec_() != QDialog.Accepted:
            return
        existing = set(self._property_names())
        for name in picker.selected():
            if name in existing:
                continue
            self._append_row(name, "", True, True, "plain")

    def delete_rows(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()},
                      reverse=True)
        for row in rows:
            self.table.removeRow(row)

    def scan_fields(self) -> None:
        """扫描目标页面：识别 locator 并回填（扫描失败只提示，不清空已有值）。"""
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.information(self, "提示", "先填目标 URL")
            return
        properties = self._property_names()
        if not properties:
            QMessageBox.information(self, "提示", "先添加要填的属性")
            return

        self.status_label.setText("正在扫描页面字段……")
        try:
            from PyQt5.QtWidgets import QApplication

            QApplication.processEvents()
            from page.matcher import FieldMatcher
            from page.scanner import Scanner

            fields = Scanner().scan(url, context=self._collect_context())
            mapping = FieldMatcher().match(fields, properties)
        except Exception as e:  # noqa: BLE001 — 扫描失败不该中断编辑
            QMessageBox.warning(self, "扫描失败", str(e)[:400])
            self.status_label.setText("扫描失败（可手工填 locator）")
            return

        hits = 0
        for row in range(self.table.rowCount()):
            locator = mapping.get(self._cell_text(row, 0)) or ""
            if locator:
                self._set_cell_text(row, 1, locator)
                hits += 1
        self.status_label.setText(f"扫描完成：{hits}/{len(properties)} 个属性命中 locator")

    def import_url_from_qr(self) -> None:
        """从二维码图片解出真实 URL（微信 H5 场景）。"""
        from PyQt5.QtWidgets import QFileDialog

        from core.qr_decode import decode_qr_text

        path, _ = QFileDialog.getOpenFileName(
            self, "选择二维码图片", "", "图片 (*.png *.jpg *.jpeg *.bmp)")
        if not path:
            return
        try:
            text = decode_qr_text(path) or ""
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "解码失败", str(e))
            return
        if text.lower().startswith(("http://", "https://")):
            self.url_edit.setText(text.strip())
            self.status_label.setText("已从二维码取到 URL")
        else:
            QMessageBox.warning(self, "不是网址", f"二维码内容是：{text[:100]}")

    def save(self) -> bool:
        product = self.collect_product()
        if not product.name:
            QMessageBox.warning(self, "保存失败", "请填品种名称")
            return False
        if not product.field_mapping:
            QMessageBox.warning(self, "保存失败", "至少添加一个属性")
            return False
        empty = [m.property for m in product.field_mapping if not m.locator]
        if empty:
            answer = QMessageBox.question(
                self, "有属性没有 locator",
                "这些属性还没有 locator，将不会被填写：\n  "
                + "、".join(empty) + "\n\n仍然保存吗？")
            if answer != QMessageBox.Yes:
                return False
        if product.name != self._original_name:
            for existing in self.library.list_products():
                if existing.name == product.name:
                    answer = QMessageBox.question(
                        self, "已存在同名品种",
                        f"「{product.name}」已存在，覆盖它吗？")
                    if answer != QMessageBox.Yes:
                        return False
                    break
        try:
            path = self.library.save(product)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(e))
            return False

        self._original_name = product.name
        self._set_title(product.name)
        self.status_label.setText(f"已保存：{path}")
        self.saved.emit(product.name)
        return True

    def save_as(self) -> None:
        """另存为：改个名字（基础品种 + 机构差异）。"""
        from PyQt5.QtWidgets import QInputDialog

        name, ok = QInputDialog.getText(self, "另存为", "新名称：",
                                        text=(self.name_edit.text() or "") + "_副本")
        if not ok or not name.strip():
            return
        self.name_edit.setText(name.strip())
        self.save()

    def delete_product(self) -> None:
        """删除当前品种（yaml 文件）。"""
        name = self._original_name or self.name_edit.text().strip()
        if not name:
            QMessageBox.information(self, "提示", "当前没有已保存的品种")
            return
        if QMessageBox.question(self, "删除品种",
                                f"确定删除品种「{name}」吗？（删掉它的 yaml 配置文件）"
                                ) != QMessageBox.Yes:
            return

        import os

        target = os.path.join(self.library.library_dir,
                              f"{ProductLibrary._safe_filename(name)}.yaml")
        try:
            if os.path.exists(target):
                os.remove(target)
        except OSError as e:
            QMessageBox.critical(self, "删除失败", str(e))
            return
        self.status_label.setText(f"已删除：{name}")
        self.load_product(None)
        self.saved.emit("")

    # ------------------------------------------------------------------
    # 表格辅助
    # ------------------------------------------------------------------
    def _append_row(self, prop: str, locator: str, required: bool,
                    verify: bool, fill_mode: str) -> int:
        row = self.table.rowCount()
        self.table.insertRow(row)

        self._set_cell_text(row, 0, prop)
        self._set_cell_text(row, 1, locator)
        for col, checked in ((2, required), (3, verify)):
            item = QTableWidgetItem()
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.table.setItem(row, col, item)

        combo = QComboBox()
        for mode in FILL_MODES:
            combo.addItem(_FILL_MODE_HINT[mode], mode)
        index = combo.findData(fill_mode or "plain")
        combo.setCurrentIndex(index if index >= 0 else 0)
        self.table.setCellWidget(row, 4, combo)
        return row

    def _property_names(self) -> List[str]:
        return [self._cell_text(row, 0) for row in range(self.table.rowCount())
                if self._cell_text(row, 0)]

    def _collect_context(self) -> dict:
        context: dict = {}
        if self.mobile_check.isChecked():
            context["mobile"] = True
        if self.wechat_check.isChecked():
            context["wechat"] = True
        referer = self.referer_edit.text().strip()
        if referer:
            context["referer"] = referer
        return context

    def _row_mode(self, row: int) -> str:
        combo = self.table.cellWidget(row, 4)
        if isinstance(combo, QComboBox):
            return str(combo.currentData() or "plain")
        return "plain"

    def _cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text().strip() if item else ""

    def _cell_checked(self, row: int, col: int) -> bool:
        item = self.table.item(row, col)
        return bool(item and item.checkState() == Qt.Checked)

    def _set_cell_text(self, row: int, col: int, text: str) -> None:
        item = self.table.item(row, col)
        if item is None:
            self.table.setItem(row, col, QTableWidgetItem(text))
        else:
            item.setText(text)

    def _move_row(self, delta: int) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if len(rows) != 1:
            return
        row = rows[0]
        new_row = row + delta
        if not (0 <= new_row < self.table.rowCount()):
            return
        # 用「快照 → 交换」实现整行搬移（含勾选状态与下拉）
        snapshot = [self._cell_text(row, 0), self._cell_text(row, 1),
                    self._cell_checked(row, 2), self._cell_checked(row, 3),
                    self._row_mode(row)]
        other = [self._cell_text(new_row, 0), self._cell_text(new_row, 1),
                 self._cell_checked(new_row, 2), self._cell_checked(new_row, 3),
                 self._row_mode(new_row)]
        for target, data in ((row, other), (new_row, snapshot)):
            self._set_cell_text(target, 0, data[0])
            self._set_cell_text(target, 1, data[1])
            self.table.item(target, 2).setCheckState(
                Qt.Checked if data[2] else Qt.Unchecked)
            self.table.item(target, 3).setCheckState(
                Qt.Checked if data[3] else Qt.Unchecked)
            combo = self.table.cellWidget(target, 4)
            if isinstance(combo, QComboBox):
                combo.setCurrentIndex(combo.findData(data[4]))
        self.table.selectRow(new_row)

    def _set_title(self, name: str) -> None:
        self.title_label.setText(f"品种编辑 · {name}" if name else "品种编辑（新建）")


__all__ = ["ProductPage", "FILL_MODES"]
