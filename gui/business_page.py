"""业务入口页：**品种清单 + 每个品种的操作**（单条录入 / 批量录入）。

为什么不放两张通用卡片（原来那样）："单条录入"会跑到主界面去、"批量录入"留在这个页面，
**同一件事的两个入口分在两个地方**。现在按「品种 → 操作」排，
同一个品种的两种录入方式就在同一行，起点一致；
点「单条录入」= 回主界面并选中该品种（在那里填值、点开始录入），
点「批量录入」= 进批量页并预选该品种。

新增业务（培训报名 / 安全评价）以后按同样的形状挂进来。
"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QGroupBox, QHBoxLayout, QLabel, QPushButton,
                             QScrollArea, QVBoxLayout, QWidget)

from core.product_library import ProductLibrary

from .theme import mark


class BusinessPage(QWidget):
    """保险业务：品种清单 + 每个品种的两个操作。"""

    #: 参数都是品种名
    single_requested = pyqtSignal(str)
    batch_requested = pyqtSignal(str)

    def __init__(self, library: ProductLibrary = None, parent=None):
        super().__init__(parent)
        self.library = library or ProductLibrary()
        self._row_widgets = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(10)
        outer.addWidget(mark(QLabel("🏢 保险业务"), "title"))

        hint = QLabel("每个品种下面挂着自己的操作 —— 单条录入（回主界面填值）"
                      "或批量录入（用台账）。两种方式从同一个地方出发。")
        hint.setWordWrap(True)
        outer.addWidget(mark(hint, "hint"))

        box = QGroupBox("品种（录入目标）")
        box_layout = QVBoxLayout(box)
        inner = QWidget()
        self._rows_layout = QVBoxLayout(inner)
        self._rows_layout.setSpacing(6)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        box_layout.addWidget(scroll)
        outer.addWidget(box, 1)

        plan = QLabel("待接入业务：培训报名（报名收单）· 安全评价（长流程 + 审批）")
        outer.addWidget(mark(plan, "hint"))

        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """按品种库重建清单（每次进入页面时调，配置改了就能看到）。"""
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._row_widgets = []

        products = []
        try:
            products = self.library.list_products()
        except Exception:  # noqa: BLE001 — 读不到库不该让页面打不开
            products = []

        if not products:
            empty = QLabel("品种库还是空的 —— 先到工具栏「+ 新建品种」建一个")
            self._rows_layout.addWidget(mark(empty, "hint"))
            return

        for product in products:
            self._rows_layout.addWidget(self._build_row(product))
        self._rows_layout.addStretch()

    def _build_row(self, product) -> QWidget:
        """一行：品种名（带目标页面提示） + 单条录入 + 批量录入。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)

        name = QLabel(product.name)
        name.setMinimumWidth(240)
        name.setToolTip(product.target_url or "（未配目标 URL）")
        layout.addWidget(name)
        url = QLabel(product.target_url or "（未配目标 URL）")
        mark(url, "hint")
        url.setMinimumWidth(320)
        layout.addWidget(url, 1)

        single = QPushButton("🖥️ 单条录入")
        single.setToolTip("回主界面并选中这个品种：填值 → 开始录入（带批注核对）")
        single.clicked.connect(lambda _=False, n=product.name:
                               self.single_requested.emit(n))
        layout.addWidget(single)

        batch = QPushButton("📋 批量录入")
        mark(batch, "primary")
        batch.setToolTip("进批量录入页（已预选这个品种）：导入台账 → 串行填表 → 复核")
        batch.clicked.connect(lambda _=False, n=product.name:
                              self.batch_requested.emit(n))
        layout.addWidget(batch)

        self._row_widgets.append((product.name, single, batch))
        return row


__all__ = ["BusinessPage"]
