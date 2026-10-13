"""主窗口（MAA 风格三段式布局）。"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QMainWindow, QSplitter, QToolBar, QStatusBar,
                             QLineEdit, QPushButton, QMessageBox,
                             QStackedWidget)

from .product_list_panel import ProductListPanel
from .property_panel import PropertyPanel
from .page_panel import PagePanel
from .theme import mark


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FormPilot - 自动表单录入助手")
        self.resize(1280, 800)

        self._build_toolbar()
        self._build_splitter()
        self._connect()

        self.statusBar().showMessage("就绪")

    def _build_toolbar(self):
        toolbar = QToolBar()
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.new_action = toolbar.addAction("+ 新建品种")
        self.business_action = toolbar.addAction("🏢 保险")
        self.business_action.setToolTip("保险业务：批量录入 / 台账复核 / 单条录入")
        self.load_action = toolbar.addAction("📂 载入数据")
        self.load_action.setToolTip("从 Excel / CSV 取第一条记录，"
                                    "按配方的列名字典映射后填进属性面板")
        self.prop_mgmt_action = toolbar.addAction("🏷️ 属性管理")
        self.prop_mgmt_action.setToolTip("创建/编辑属性对象（名称、来源、类型、选项、默认值）")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("目标页面 URL...")
        self.url_input.setFixedWidth(380)
        toolbar.addWidget(self.url_input)

        self.start_btn = QPushButton("🚀 开始录入")
        mark(self.start_btn, "primary")
        toolbar.addWidget(self.start_btn)

    def _build_splitter(self):
        # 全局唯一的属性库实例：面板/向导/属性管理共用，保存后即刻生效
        from core.property_def import PropertyDefLibrary
        self.prop_def_library = PropertyDefLibrary()

        splitter = QSplitter(Qt.Horizontal)

        self.product_list = ProductListPanel()
        splitter.addWidget(self.product_list)

        self.property_panel = PropertyPanel(prop_def_library=self.prop_def_library)
        splitter.addWidget(self.property_panel)

        self.page_panel = PagePanel()
        splitter.addWidget(self.page_panel)

        splitter.setSizes([250, 500, 530])

        # 中央 QStackedWidget：页 0 = 三栏工作区，页 1 = 属性管理内部页面，
        # 页 2 = 业务入口页（保险），页 3 = 保险业务下的批量录入页
        from .property_manager_page import PropertyManagerWidget
        self.stack = QStackedWidget()
        self.stack.addWidget(splitter)
        self.property_page = PropertyManagerWidget(self.prop_def_library)
        self.property_page.back_requested.connect(self._show_main_page)
        self.stack.addWidget(self.property_page)
        # 业务页与批量页都**首次打开时才构造**（避免启动阶段就去碰数据库）
        self.business_page = None
        self.batch_page = None
        # 品种编辑页（取代原来的 5 步向导；创建与编辑共用）
        self.product_page = None
        self.setCentralWidget(self.stack)

    def _show_main_page(self):
        """从内部页面返回三栏工作区，并按更新后的属性库重建属性面板。

        这是所有"返回"路径的唯一出口 —— 品种编辑页有未保存修改时先问一句，
        免得返回把一屏编辑悄悄吃掉。
        """
        if (self.product_page is not None
                and self.stack.currentWidget() is self.product_page
                and not self.product_page.confirm_leave()):
            return
        self.stack.setCurrentIndex(0)
        self.prop_mgmt_action.setText("🏷️ 属性管理")
        self.business_action.setText("🏢 保险")
        self.new_action.setText("+ 新建品种")

        product = self.page_panel._product
        if product is not None:
            # 先收集用户已输入的值，重建面板后回填，避免刷新丢数据
            values = self._collect_panel_values()
            self.property_panel.show_for_product(product)
            for group in self.property_panel.source_groups.values():
                group.set_values(values)
        self.statusBar().showMessage("属性库已更新")

    def _connect(self):
        self.product_list.product_selected.connect(self._on_product_selected)
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.page_panel.fill_btn.clicked.connect(self._on_start_clicked)
        self.property_panel.source_extracted.connect(self._on_source_extracted)
        self.new_action.triggered.connect(self._on_new_product)
        self.product_list.new_btn.clicked.connect(self._on_new_product)
        self.product_list.edit_requested.connect(self._on_edit_product)
        self.prop_mgmt_action.triggered.connect(self._on_manage_properties)
        self.business_action.triggered.connect(self._on_open_business)
        self.load_action.triggered.connect(self._on_load_data)

    def _on_source_extracted(self, source_name: str, values: dict):
        """拖图识别完成：回填面板后，把「品种属性名」喂入属性池。"""
        if "_error" in values:
            self.statusBar().showMessage(f"{source_name} 识别失败：{values['_error']}")
            return

        src = values.get("_source")
        # 注意：不能直接把识别结果的原始 key（如 "姓名"、"身份证号"）喂入
        # 属性池——填表用的是品种属性名（如 "学生姓名"），原始 key 会污染池子。
        # SourceGroup._process_input 里 set_values() 已经把结果回填到输入框，
        # 所以这里收集该来源组当前输入框的值（key 即品种属性名）。
        group = self.property_panel.source_groups.get(source_name)
        if group is not None:
            self.page_panel.feed_input(group.collect_values())

        reason = values.get("_fallback_reason")
        if reason:
            # 真实 OCR 调用失败已回退 Mock：把原因告诉用户（如服务未开通）
            self.statusBar().showMessage(f"{source_name}：{reason}")
        elif src == "mock":
            self.statusBar().showMessage(f"{source_name} 识别完成（Mock 数据）")
        else:
            self.statusBar().showMessage(f"{source_name} 识别完成")

    def _on_product_selected(self, product):
        self.property_panel.show_for_product(product)
        self.page_panel.set_product(product)
        if product.target_url:
            self.url_input.setText(product.target_url)
        self.statusBar().showMessage(f"已选择品种：{product.name}")

    def _on_start_clicked(self):
        product = self.page_panel._product
        if product is None:
            QMessageBox.information(self, "提示", "请先在左侧选择一个品种")
            return
        # 从属性面板收集当前输入喂入属性池，再执行填表。
        values = self._collect_panel_values()
        self._persist_reusable_sources()
        self.page_panel.feed_input(values)
        self.page_panel.run_fill()
        self.statusBar().showMessage("填表完成（见右下报告）")

    def _persist_reusable_sources(self):
        """把可复用的手动配置（保险员信息）持久化到本地，下次启动自动预填。"""
        group = self.property_panel.source_groups.get("保险员信息")
        if group is None or group.source is None:
            return
        data = group.collect_values()
        if not any(str(v).strip() for v in data.values()):
            return  # 全空不覆盖已保存的配置
        try:
            group.source.extract(data)   # 写回来源内部状态
            group.source.save()          # 持久化到 config/global_user_info.yaml
        except Exception as e:  # noqa: BLE001 — 持久化失败不阻塞填表
            self.statusBar().showMessage(f"保险员信息保存失败：{e}")

    def _collect_panel_values(self) -> dict:
        """从属性面板各分组收集输入框值。"""
        values = {}
        for source_name, group in self.property_panel.source_groups.items():
            values.update(group.collect_values())
        return values

    def _on_new_product(self):
        """工具栏「+ 新建品种」：打开品种编辑页（新建）。

        已经站在品种页上时，这个按钮的文案是「⬅ 返回主界面」——那就返回，
        而不是再把表单清空重开（原来就是这样，用户反馈"左上角返回不好用"）。
        """
        if (self.product_page is not None
                and self.stack.currentWidget() is self.product_page):
            self._show_main_page()
            return
        self._open_product_page(None)

    def _on_edit_product(self, name: str):
        """打开品种编辑页（编辑已有品种）。"""
        for product in self.product_list.products.values():
            if product.name == name:
                self._open_product_page(product)
                return
        self.statusBar().showMessage(f"未找到品种：{name}")

    def _open_product_page(self, product=None) -> None:
        """品种编辑页：一屏编完（取代原来的 5 步向导；创建与编辑共用）。"""
        if self.product_page is None:
            from .product_page import ProductPage

            self.product_page = ProductPage(prop_def_library=self.prop_def_library)
            self.product_page.saved.connect(self._on_product_saved)
            self.product_page.back_requested.connect(self._show_main_page)
            self.stack.addWidget(self.product_page)
        self.product_page.load_product(product)
        self.stack.setCurrentWidget(self.product_page)
        self.new_action.setText("⬅ 返回主界面")
        self.statusBar().showMessage(
            f"品种编辑：{product.name}" if product is not None else "品种编辑：新建")

    def _on_product_saved(self, name: str) -> None:
        """保存成功：刷新左侧列表并选中它（主界面面板跟着加载）。"""
        self.product_list.refresh()
        if not name:
            self.statusBar().showMessage("品种已删除")
            return
        for i in range(self.product_list.list_widget.count()):
            item = self.product_list.list_widget.item(i)
            if item.data(Qt.UserRole) == name:
                self.product_list.list_widget.setCurrentRow(i)
                break
        self.statusBar().showMessage(f"品种已保存：{name}")

    def _on_manage_properties(self):
        """切换到属性管理内部页面；再次点击返回主界面。"""
        if self.stack.currentIndex() == 1:
            self._show_main_page()
            return
        self.property_page.refresh_table()
        self.stack.setCurrentIndex(1)
        self.prop_mgmt_action.setText("⬅ 返回主界面")
        self.statusBar().showMessage("属性管理：编辑后记得点「💾 保存」")

    def _on_open_business(self):
        """打开业务入口页（保险）；再次点击返回主界面。

        结构是「业务 → 操作 → 具体页面」：批量录入不是工具栏上的一级入口，
        而是保险业务下的一项操作（以后还会有培训报名 / 安全评价）。
        """
        if self.business_page is None:
            from .business_page import BusinessPage

            self.business_page = BusinessPage()
            self.business_page.open_batch.connect(self._open_batch_page)
            self.business_page.open_single.connect(self._show_main_page)
            self.stack.addWidget(self.business_page)
        elif self.stack.currentWidget() is self.business_page:
            self._show_main_page()
            return
        self.stack.setCurrentWidget(self.business_page)
        self.business_action.setText("⬅ 返回主界面")
        self.statusBar().showMessage("保险业务：选一项操作（批量录入 / 单条录入）")

    def _open_batch_page(self):
        """进入保险业务下的批量录入页（首次打开才构造，避免启动就碰数据库）。"""
        if self.batch_page is None:
            from .batch_page import BatchPage

            self.batch_page = BatchPage()
            self.batch_page.back_requested.connect(self._on_open_business)
            self.stack.addWidget(self.batch_page)
        self.batch_page.refresh_batches()
        self.stack.setCurrentWidget(self.batch_page)
        self.business_action.setText("⬅ 返回主界面")
        self.statusBar().showMessage("保险 › 批量录入：导入台账 → 批量录入 → 逐条复核")

    # ------------------------------------------------------------------
    # 载入数据（Excel / CSV → 属性面板）
    # ------------------------------------------------------------------
    def _on_load_data(self) -> None:
        """从表格取一条记录，按配方的列名字典映射后填进属性面板。"""
        from PyQt5.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self, "载入一条数据（Excel / CSV 第一条）", "",
            "表格 (*.xlsx *.xls *.csv)")
        if not path:
            return
        try:
            values = self._record_from_file(path)
        except Exception as e:  # noqa: BLE001 — 读表失败只提示，不崩
            QMessageBox.warning(self, "载入失败", str(e))
            return
        if not values:
            QMessageBox.warning(
                self, "载入失败",
                "没认出任何字段——请检查表头是否在配方的列名字典里，"
                "或先补进 config/import_mapping.yaml")
            return

        self.page_panel.feed_input(values)
        for group in self.property_panel.source_groups.values():
            group.set_values(values)
        self.statusBar().showMessage(
            f"已载入 {os.path.basename(path)}：{len(values)} 个字段")

    @staticmethod
    def _record_from_file(path: str) -> dict:
        """读表头 + 第一条数据 → {品种属性名: 值}（复用导入用的列名字典）。"""
        import yaml

        from batch.importer import ColumnMapper, read_table
        from batch.recipe import Recipe

        recipe = Recipe.load()
        with open(recipe.mapping_path, "r", encoding="utf-8") as f:
            mapper = ColumnMapper(yaml.safe_load(f) or {})

        headers, rows = read_table(path)
        if not rows:
            return {}
        column_map = mapper.resolve(headers)          # {明细字段名: 源列名}
        inverse = {field: prop for prop, field in recipe.property_map.items()}
        row = rows[0]

        values = {}
        for fieldname, column in column_map.items():
            prop = inverse.get(fieldname)
            if not prop:
                continue
            value = str(row.get(column) or "").strip()
            if value:
                values[prop] = value
        return values