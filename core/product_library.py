"""品种库管理器 — 读写 config/product_library/ 下的 yaml 品种。"""
from __future__ import annotations

import os
from typing import List, Optional

import yaml

from .product import Product


class ProductLibrary:
    """品种库：加载 / 列出 / 保存品种配置。"""

    def __init__(self, library_dir: Optional[str] = None):
        if library_dir is None:
            library_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "product_library",
            )
        self.library_dir = library_dir

    def list_products(self) -> List[Product]:
        """列出库中所有品种（跳过 _template.yaml）。"""
        products: List[Product] = []
        if not os.path.isdir(self.library_dir):
            return products
        for fname in sorted(os.listdir(self.library_dir)):
            if not fname.endswith(".yaml") or fname.startswith("_"):
                continue
            product = self.load(os.path.join(self.library_dir, fname))
            if product is not None:
                products.append(product)
        return products

    def load(self, path: str) -> Optional[Product]:
        """从单个 yaml 文件加载品种。"""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return Product.from_dict(data)

    def save(self, product: Product) -> str:
        """保存品种到 yaml（文件名用品种名，安全化处理）。

        :return: 保存的文件完整路径
        """
        safe_name = self._safe_filename(product.name)
        path = os.path.join(self.library_dir, f"{safe_name}.yaml")

        serialized = product.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(serialized, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
        return path

    @staticmethod
    def _safe_filename(name: str) -> str:
        """把品种名转为安全的文件名（保留中文，替换非法字符）。"""
        unsafe = '<>:"/\\|?*'
        for ch in unsafe:
            name = name.replace(ch, "_")
        return name.strip() or "新品种"