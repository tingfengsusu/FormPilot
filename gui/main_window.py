"""主窗口（MAA 风格三段式布局）。"""
from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QMainWindow, QSplitter, QToolBar, QStatusBar,
                             QLineEdit, QPushButton, QMessageBox,
                             QStackedWidget)

from .product_list_panel import ProductListPanel
from .property_panel import PropertyPanel
from .page_panel import PagePanel


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
        toolbar.addAction("📂 载入")
        self.prop_mgmt_action = toolbar.addAction("🏷️ 属性管理")
        self.prop_mgmt_action.setToolTip("创建/编辑属性对象（名称、来源、类型、选项、默认值）")
        self.batch_action = toolbar.addAction("📋 批量投保")
        self.batch_action.setToolTip("学平险批量录入：导入台账 → 批量填表 → 逐条复核")

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("目标页面 URL...")
        self.url_input.setFixedWidth(380)
        toolbar.addWidget(self.url_input)

        self.start_btn = QPushButton("🚀 开始录入")
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
        # 页 2 = 批量投保页（都是程序内切换，不弹新窗口）
        from .property_manager_page import PropertyManagerWidget
        self.stack = QStackedWidget()
        self.stack.addWidget(splitter)
        self.property_page = PropertyManagerWidget(self.prop_def_library)
        self.property_page.back_requested.connect(self._show_main_page)
        self.stack.addWidget(self.property_page)
        # 批量投保页**首次打开时才构造**（避免启动阶段就去碰数据库）
        self.batch_page = None
        self.setCentralWidget(self.stack)

    def _show_main_page(self):
        """从内部页面返回三栏工作区，并按更新后的属性库重建属性面板。"""
        self.stack.setCurrentIndex(0)
        self.prop_mgmt_action.setText("🏷️ 属性管理")
        self.batch_action.setText("📋 批量投保")

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
        self.prop_mgmt_action.triggered.connect(self._on_manage_properties)
        self.batch_action.triggered.connect(self._on_open_batch)

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
        """打开品种创建向导，完成后保存并刷新列表。"""
        from .product_wizard import ProductWizard

        wizard = ProductWizard(self, prop_def_library=self.prop_def_library)
        if wizard.exec_() == ProductWizard.Accepted:
            try:
                product = wizard.collect_product()
                path = wizard.library.save(product)
                self.product_list.refresh()
                self.statusBar().showMessage(f"新品种已保存：{product.name}")
            except Exception as e:
                QMessageBox.critical(self, "保存失败", str(e))

    def _on_manage_properties(self):
        """切换到属性管理内部页面；再次点击返回主界面。"""
        if self.stack.currentIndex() == 1:
            self._show_main_page()
            return
        self.property_page.refresh_table()
        self.stack.setCurrentIndex(1)
        self.prop_mgmt_action.setText("⬅ 返回主界面")
        self.statusBar().showMessage("属性管理：编辑后记得点「💾 保存」")

    def _on_open_batch(self):
        """切换到批量投保页；再次点击返回主界面。"""
        if self.batch_page is None:
            from .batch_page import BatchPage

            self.batch_page = BatchPage()
            self.stack.addWidget(self.batch_page)
        elif self.stack.currentIndex() == self.stack.indexOf(self.batch_page):
            self._show_main_page()
            return
        self.batch_page.refresh_batches()
        self.stack.setCurrentWidget(self.batch_page)
        self.batch_action.setText("⬅ 返回主界面")
        self.statusBar().showMessage("批量投保：导入台账 → 批量录入 → 逐条复核")