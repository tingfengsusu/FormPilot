"""单个属性输入框。

一个属性（如「姓名」）对应一个带标签的输入行，支持：
  - 只读展示 OCR 结果
  - 依据属性对象（PropertyDef）渲染：
      * select 类型 → 下拉框（候选项来自属性定义）
      * 默认值自动预填
      * 提示语按类型给出格式提示

上传入口在分组框底部（SourceGroup 的「📤 上传输入」），本组件不再带
行级上传按钮。
"""
from __future__ import annotations

from typing import Optional

from PyQt5.QtWidgets import QComboBox, QHBoxLayout, QLabel, QLineEdit, QWidget

from core.property_def import PropertyDef, TYPE_DATE, TYPE_PHONE

#: 类型 → 输入框占位提示
_PLACEHOLDERS = {
    TYPE_DATE: "格式：YYYY-MM-DD",
    TYPE_PHONE: "11位手机号",
}


class PropertyField(QWidget):
    """单个属性输入行（支持文本框 / 下拉框两种形态）。"""

    def __init__(self, field_name: str, field_def: Optional[PropertyDef] = None,
                 parent=None):
        super().__init__(parent)
        self.field_name = field_name
        self.field_def = field_def

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)

        self.label = QLabel(field_name)
        self.label.setMinimumWidth(120)
        layout.addWidget(self.label)

        if field_def is not None and field_def.field_type == "select" and field_def.options:
            # 下拉形态：候选项来自属性定义
            self.value_edit = QComboBox()
            self.value_edit.addItem("")  # 空选项 = 待选择
            self.value_edit.addItems(field_def.options)
            layout.addWidget(self.value_edit, 1)
            self._combo = True
        else:
            self.value_edit = QLineEdit()
            if field_def is not None and field_def.default_value:
                self.value_edit.setPlaceholderText(field_def.default_value)
            else:
                self.value_edit.setPlaceholderText(
                    _PLACEHOLDERS.get(field_def.field_type, "待填写")
                    if field_def is not None else "待填写")
            layout.addWidget(self.value_edit, 1)
            self._combo = False

        # 默认值预填
        if field_def is not None and field_def.default_value:
            self.set_value(field_def.default_value)

    def set_value(self, value) -> None:
        if value is None or str(value) == "":
            return
        if self._combo:
            text = str(value)
            combo = self.value_edit
            idx = combo.findText(text)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                # 识别结果不在候选里：临时插入以便显示，不丢值
                combo.addItem(text)
                combo.setCurrentIndex(combo.count() - 1)
        else:
            self.value_edit.setText(str(value))

    def value(self) -> str:
        if self._combo:
            return self.value_edit.currentText().strip()
        return self.value_edit.text().strip()

