"""属性来源分组框。

一个「属性来源」（如身份证OCR）由一个 SourceGroup 呈现，内部按该来源能
提供的字段列表创建 PropertyField 输入行，分组底部提供唯一的
「📤 上传输入」入口（属性行内不再有按钮）。

支持：
  - 拖拽图片/文件到分组框，自动触发来源的 extract() 并回填字段
  - 上传图片后在分组内显示缩略预览（点击可查看大图），
    便于用户人工核对 OCR 结果与证件原图是否一致
  - 手动配置型来源（如保险员信息）隐藏上传入口
"""
from __future__ import annotations

import os
from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (QApplication, QDialog, QGroupBox, QLabel,
                             QPushButton, QVBoxLayout)

from .property_field import PropertyField
from core.property_source import PropertySource
from core.property_def import PropertyDef

#: 可预览的图片扩展名（Excel 等数据文件暂不做预览）
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

#: 缩略图最大高度
_THUMB_HEIGHT = 180


class _ClickableLabel(QLabel):
    """可点击的 QLabel（用于缩略图放大查看）。"""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):  # noqa: N802 — Qt 命名
        self.clicked.emit()
        super().mousePressEvent(event)


class ImagePreviewDialog(QDialog):
    """大图预览对话框（点击缩略图打开）。"""

    def __init__(self, image_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"预览 - {os.path.basename(image_path)}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        label = QLabel()
        label.setAlignment(Qt.AlignCenter)
        pix = QPixmap(image_path)
        screen = QApplication.primaryScreen()
        if screen is not None and not pix.isNull():
            geo = screen.availableGeometry()
            pix = pix.scaled(int(geo.width() * 0.7), int(geo.height() * 0.8),
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        label.setPixmap(pix)
        layout.addWidget(label)
        self.resize(label.sizeHint())


class SourceGroup(QGroupBox):
    """单个属性来源的分组框（支持拖拽上传 + 图片预览）。"""

    #: 拖拽/上传后、识别完成时发出：dict（识别结果字段值）
    extracted = pyqtSignal(str, dict)  # (source_name, values)

    def __init__(self, source_name: str, fields: List[str],
                 source: Optional[PropertySource] = None,
                 field_defs: Optional[dict] = None, parent=None):
        super().__init__(f"📌 {source_name}", parent)
        self.source_name = source_name
        self._source = source  # 关联的 PropertySource 实例（用于 extract）
        self._preview_path: Optional[str] = None
        field_defs = field_defs or {}

        # 修复标题下半部分被截断的问题。
        self.setStyleSheet(
            "QGroupBox {"
            "  padding-top: 14px;"
            "  margin-top: 8px;"
            "  font-weight: bold;"
            "}"
            "QGroupBox::title {"
            "  subcontrol-origin: margin;"
            "  subcontrol-position: top left;"
            "  left: 12px;"
            "  padding: 0 4px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 16, 8, 8)
        self.field_widgets = {}

        for f in fields:
            pf = PropertyField(f, field_def=field_defs.get(f))
            self.field_widgets[f] = pf
            layout.addWidget(pf)

        # 分组底部唯一的上传入口；手动配置型来源不显示
        self.upload_btn = QPushButton("📤 上传输入")
        self.upload_btn.setToolTip("选择图片/文件，或直接将文件拖入本分组框")
        self.upload_btn.clicked.connect(self._on_upload)
        layout.addWidget(self.upload_btn)

        accepts_file = source is None or getattr(source, "accepts_file_input", True)
        self.upload_btn.setVisible(accepts_file)
        if not accepts_file:
            self.setAcceptDrops(False)
            self.upload_btn.setToolTip("")

        # 图片预览区（上传图片后显示，点击看大图）
        self.preview_label = _ClickableLabel()
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setCursor(Qt.PointingHandCursor)
        self.preview_label.setToolTip("点击查看大图")
        self.preview_label.setVisible(False)
        self.preview_label.clicked.connect(self._on_preview_clicked)
        layout.addWidget(self.preview_label)

        # 启用拖拽：接受图片或任意文件
        if accepts_file:
            self.setAcceptDrops(True)

    # ------------------------------------------------------------------
    # 字段读写
    # ------------------------------------------------------------------
    def set_values(self, values: dict) -> None:
        """按字段字典填充输入框。

        支持「源字段名 → 品种属性名」的映射：
        例如来源返回 "姓名"（无前缀），而品种属性叫 "学生姓名"，
        通过「源字段名包含在属性名里」的规则反向匹配。
        """
        for name, w in self.field_widgets.items():
            matched = self._match_value(name, values)
            w.set_value(matched)

    def _match_value(self, prop_name: str, values: dict):
        """按属性名从识别结果里找值，找不到返回 None。"""
        # 1. 精确匹配
        if prop_name in values:
            return values[prop_name]
        # 2. 反向包含：来源字段名（如 "姓名"）是属性名（"学生姓名"）的子串
        for src_field, v in values.items():
            if src_field.startswith("_"):
                continue
            if src_field and src_field in prop_name:
                return v
        return None

    def collect_values(self) -> dict:
        """收集当前输入框里的值。"""
        return {name: w.value() for name, w in self.field_widgets.items()}

    # ------------------------------------------------------------------
    # 图片预览
    # ------------------------------------------------------------------
    def _show_preview(self, path: str) -> None:
        """上传图片后在分组内显示缩略图；非图片文件不预览。"""
        ext = os.path.splitext(path)[1].lower()
        if ext not in _IMAGE_EXTS:
            self.preview_label.setVisible(False)
            self._preview_path = None
            return

        pix = QPixmap(path)
        if pix.isNull():
            return
        self._preview_path = path
        max_w = max(self.width() - 24, 220)
        self.preview_label.setPixmap(pix.scaled(
            max_w, _THUMB_HEIGHT, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        self.preview_label.setVisible(True)

    def _on_preview_clicked(self) -> None:
        if self._preview_path and os.path.exists(self._preview_path):
            ImagePreviewDialog(self._preview_path, self).exec_()

    # ------------------------------------------------------------------
    # 拖拽上传
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):  # noqa: N802 — Qt 命名
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self.styleSheet() + " QGroupBox { border: 2px dashed #3a7afe; }")
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802 — Qt 命名
        self._restore_border()
        event.accept()

    def dropEvent(self, event):  # noqa: N802 — Qt 命名
        self._restore_border()
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            path = url.toLocalFile()
            if path:
                self._process_input(path)
        event.acceptProposedAction()

    def _restore_border(self):
        self.setStyleSheet(self.styleSheet().replace(
            " QGroupBox { border: 2px dashed #3a7afe; }", ""))

    # ------------------------------------------------------------------
    # 上传按钮 / 触发识别
    # ------------------------------------------------------------------
    def _on_upload(self):
        from PyQt5.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, f"选择 {self.source_name} 的输入",
            "", "图片/文件 (*.png *.jpg *.jpeg *.bmp *.webp *.xlsx *.xls *.csv)")
        if path:
            self._process_input(path)

    def _process_input(self, path: str):
        """喂入输入，调用来源的 extract()，回填字段并预览图片。"""
        if self._source is None:
            return
        # 先展示原图，识别失败时用户也能看到上传了什么
        self._show_preview(path)
        try:
            result = self._source.extract(path)
            self.set_values(result)
            # 通知外部（主窗口）更新属性池
            self.extracted.emit(self.source_name, result)
        except Exception as e:  # noqa: BLE001
            # 识别失败不应崩溃，回退到空结果并提示
            self.extracted.emit(self.source_name, {"_error": str(e)})

    def bind_source(self, source: PropertySource):
        """绑定（或更换）关联的 PropertySource 实例。"""
        self._source = source

    @property
    def source(self) -> Optional[PropertySource]:
        """关联的 PropertySource 实例（可能为 None）。"""
        return self._source
