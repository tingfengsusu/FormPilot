"""中间：属性面板 — 按来源分组显示。"""
from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QScrollArea)

from core.product import Product
from .widgets.source_group import SourceGroup
from sources.factory import create_source
from .theme import mark


class PropertyPanel(QWidget):
    """中间：属性面板，根据品种切换显示需要的属性来源组。"""

    #: 某个来源识别完成（拖图后）时发出：dict 识别结果
    source_extracted = pyqtSignal(str, dict)  # (source_name, values)

    def __init__(self, parent=None, prop_def_library=None):
        super().__init__(parent)
        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.addWidget(QLabel("⚙️ 属性面板"))

        from core.property_def import PropertyDefLibrary
        self._library = prop_def_library or PropertyDefLibrary()

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(8, 8, 8, 8)
        self.scroll.setWidget(self.container)
        self._outer_layout.addWidget(self.scroll, 1)

        self.source_groups = {}  # source_name -> SourceGroup

        self._show_empty()

    def _show_empty(self) -> None:
        self._clear_groups()
        hint = QLabel("请先在左侧选择一个品种")
        mark(hint, "hint")
        self.container_layout.addWidget(hint)
        self.container_layout.addStretch()

    def _clear_groups(self) -> None:
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self.source_groups.clear()

    def show_for_product(self, product: Product):
        """根据品种显示需要的属性来源组。"""
        self._clear_groups()

        # 按来源分组：优先用属性对象（PropertyDef）声明的归属来源，
        # 属性库中不存在的属性再退回关键词推断。
        source_fields = self._build_source_fields(product)
        defs = {d.name: d for d in self._library.all_defs()}

        if not source_fields:
            hint = QLabel("该品种暂无可用属性")
            mark(hint, "hint")
            self.container_layout.addWidget(hint)
            self.container_layout.addStretch()
            return

        for source_name, fields in source_fields.items():
            # 绑定对应的 PropertySource 实例（用于拖图识别）
            source = create_source(source_name)
            group = SourceGroup(source_name, fields, source=source,
                                field_defs=defs,
                                fallback_loader=self._load_from_table)
            group.extracted.connect(self._on_source_extracted)
            self.source_groups[source_name] = group
            self.container_layout.addWidget(group)

            # 已有持久化输入的来源（如保险员信息）：预填，实现「配置一次反复用」
            if source is not None and source.is_input_ready():
                try:
                    group.set_values(source.extract(None))
                except Exception:  # noqa: BLE001 — 预填失败不阻塞面板
                    pass

        self.container_layout.addStretch()

    def _on_source_extracted(self, source_name: str, values: dict):
        """来源识别完成，转发给主窗口（用于更新属性池/状态栏）。"""
        self.source_extracted.emit(source_name, values)

    @staticmethod
    def _load_from_table(path: str) -> dict:
        """「其他」组的兜底读表：按列名字典 + property_map 读第一行。

        新业务的字段没有归属来源，全落在「其他」组——没有这个兜底，
        拖文件进去会静默无反应（实测反馈）。
        """
        from batch.importer import map_row_to_properties, read_table

        _headers, rows = read_table(path)
        if not rows:
            return {}
        return map_row_to_properties(rows[0])

    def _build_source_fields(self, product: Product) -> dict:
        """依据品种的属性名，把字段归类到各来源组。

        归类顺序：
          1. 用户自定义/覆盖的属性：按属性对象（PropertyDef）声明的 source
             分组 —— 用户的选择是权威
          2. 纯内置属性：沿用「关键词 → 来源」推断（与历史行为一致，
             避免同一属性名被多个内置来源提供时错误分组）
        """
        defs = {d.name: d for d in self._library.all_defs()}
        user_names = self._library.user_def_names()

        # 关键词 → 来源组（内置属性兜底）。
        # 顺序：更具体的关键词放前面（如「监护人」要优先于「姓名」）。
        keyword_to_source = [
            ("监护人", "Excel名单"),
            ("学校", "Excel名单"),
            ("班级", "Excel名单"),
            ("保险员", "保险员信息"),
            ("工号", "保险员信息"),
            ("所属机构", "保险员信息"),
            ("业务代码", "保险员信息"),
            ("与户主关系", "户口本OCR"),
            ("户主", "户口本OCR"),
            ("户号", "户口本OCR"),
            ("成员", "户口本OCR"),
            ("身份证号", "身份证OCR"),
            ("出生日期", "身份证OCR"),
            ("签发机关", "身份证OCR"),
            ("有效期", "身份证OCR"),
            ("性别", "身份证OCR"),
            ("民族", "身份证OCR"),
            ("地址", "身份证OCR"),
            ("姓名", "身份证OCR"),
        ]

        result: dict[str, list] = {}
        for m in product.field_mapping:
            prop = m.property

            # 1. 用户自定义/覆盖的属性按其声明的来源分组
            if prop in user_names and prop in defs:
                source = defs[prop].source
            else:
                source = None
                # 2. 纯内置属性：关键词兜底
                for keyword, src in keyword_to_source:
                    if keyword in prop:
                        source = src
                        break
                if source is None:
                    source = "其他"
            result.setdefault(source, [])
            if prop not in result[source]:
                result[source].append(prop)
        return result