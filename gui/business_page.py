"""业务入口页：先选业务，再选该业务下的操作（保险 → 批量录入 / 单条录入）。

为什么要有这一层：项目后面会承载多个业务（保险 / 安全评价 / 培训报名）。
工具栏直接摆一个"批量投保"，会让人以为程序只能干这一件事，也说不清业务边界。
结构是：**业务 → 操作 → 具体页面**；新增业务只在这里加一张卡。
"""
from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                             QWidget)

from .theme import mark


class BusinessPage(QWidget):
    """保险业务入口（操作卡片）。"""

    open_batch = pyqtSignal()
    open_single = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(12)

        outer.addWidget(mark(QLabel("🏢 保险业务"), "title"))
        hint = QLabel("先选业务，再选该业务下的操作。"
                      "后续新增业务（培训报名 / 安全评价）会并列在这里。")
        hint.setWordWrap(True)
        outer.addWidget(mark(hint, "hint"))

        cards = QHBoxLayout()
        cards.setSpacing(12)
        cards.addWidget(self._card("📋 批量录入",
                                   "导入台账 → 串行填表 → 逐条/批量复核",
                                   self.open_batch.emit, primary=True))
        cards.addWidget(self._card("🖥️ 单条录入",
                                   "在主界面填一条 → 打开浏览器核对（带批注）",
                                   self.open_single.emit))
        cards.addStretch()
        outer.addLayout(cards)
        outer.addStretch()

        plan = QLabel("待接入业务：培训报名（报名收单）· 安全评价（长流程 + 审批）")
        outer.addWidget(mark(plan, "hint"))

    @staticmethod
    def _card(title: str, desc: str, handler, primary: bool = False) -> QPushButton:
        """一张操作卡（按钮 + 说明两行；主操作用主题的 primary 角色）。"""
        button = QPushButton(f"{title}\n{desc}")
        button.setMinimumSize(280, 92)
        button.setCursor(Qt.PointingHandCursor)
        if primary:
            mark(button, "primary")
        button.clicked.connect(handler)
        return button


__all__ = ["BusinessPage"]
