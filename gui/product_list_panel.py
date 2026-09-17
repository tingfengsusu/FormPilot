"""左侧：保险品种列表 — 类似 MAA 任务清单。"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QPushButton)

from core.product import Product

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


class ProductListPanel(QWidget):
    """左侧：保险品种列表。"""

    product_selected = pyqtSignal(object)  # 发射 Product 对象

    LIBRARY_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "product_library",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("📋 保险品种"))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        self.new_btn = QPushButton("+ 新建品种")
        layout.addWidget(self.new_btn)

        self.products = {}  # display_name -> Product
        self.refresh()
        self.list_widget.currentItemChanged.connect(self._on_item_changed)

    def refresh(self) -> None:
        """从 config/product_library/ 加载品种（跳过 _template.yaml）。"""
        self.list_widget.clear()
        self.products.clear()

        if not os.path.isdir(self.LIBRARY_DIR):
            return

        for fname in sorted(os.listdir(self.LIBRARY_DIR)):
            if not fname.endswith(".yaml"):
                continue
            if fname.startswith("_"):  # 跳过模板
                continue
            path = os.path.join(self.LIBRARY_DIR, fname)
            product = self._load_product(path)
            if product is None:
                continue
            self.products[product.name] = product
            item = QListWidgetItem(product.name)
            item.setData(Qt.UserRole, product.name)
            self.list_widget.addItem(item)

    def _load_product(self, path: str):
        if yaml is None:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return Product.from_dict(data)

    def _on_item_changed(self, current, _previous):
        if current is None:
            return
        name = current.data(Qt.UserRole)
        product = self.products.get(name)
        if product is not None:
            self.product_selected.emit(product)