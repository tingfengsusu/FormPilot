"""品种创建向导 — 命名 → 选属性 → 输 URL → 扫描匹配 → 确认 → 保存。

对应设计文档 5.1 节「首次创建品种流程」。用 QWizard 实现多步向导。
"""
from __future__ import annotations

from typing import List

from PyQt5.QtWidgets import (
    QWizard, QWizardPage, QVBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QTextEdit, QHBoxLayout, QPushButton,
    QCheckBox,
)
from PyQt5.QtCore import Qt

from core.product import Product, FieldMapping
from core.product_library import ProductLibrary
from core.property_def import PropertyDefLibrary
from page.scanner import Scanner
from page.matcher import FieldMatcher


class ProductWizard(QWizard):
    """品种创建向导。"""

    def __init__(self, parent=None, library: ProductLibrary | None = None,
                 prop_def_library: PropertyDefLibrary | None = None):
        super().__init__(parent)
        self.setWindowTitle("新建品种")
        self.resize(640, 560)
        self.library = library or ProductLibrary()

        # 全部属性对象（内置 + 用户自定义），用于第 2 步多选
        self.prop_def_library = prop_def_library or PropertyDefLibrary()
        self.all_properties: List[str] = [
            d.name for d in self.prop_def_library.all_defs()]

        # 候选页面字段（第 4 步扫描结果）
        self.page_fields: List[dict] = []

        self.name_page = _NamePage()
        self.attr_page = _AttrPage(self.prop_def_library)
        self.url_page = _UrlPage()
        self.scan_page = _ScanPage(self)
        self.confirm_page = _ConfirmPage(self)

        self.addPage(self.name_page)
        self.addPage(self.attr_page)
        self.addPage(self.url_page)
        self.addPage(self.scan_page)
        self.addPage(self.confirm_page)

        # 第 3 步（URL）「下一步」时触发扫描
        self.url_page.registerField("url*", self.url_page.url_edit)
        self.setButtonText(QWizard.NextButton, "下一步")
        self.setButtonText(QWizard.FinishButton, "保存品种")

    def build_product(self) -> Product | None:
        """根据向导当前状态构建 Product 对象。

        在第 4 步扫描 + 匹配后调用，供 confirm_page 展示。
        """
        url = self.url_page.url_edit.text().strip()
        if not url:
            return None

        # 扫描页面字段（含移动端/微信 H5 上下文；Mock 模式回退示例字段）
        scanner = Scanner()
        self.page_fields = scanner.scan(url, context=self.url_page.context_config())
        self.scan_page.show_fields(self.page_fields)

        # 选中的属性
        selected = self.attr_page.selected_properties()

        # LLM 匹配
        matcher = FieldMatcher()
        mapping_result = matcher.match(self.page_fields, selected)
        self.confirm_page.set_mapping(selected, mapping_result)

        return None

    def collect_product(self) -> Product:
        """从确认页收集最终 product（含用户修正）。"""
        name = self.name_page.name_edit.text().strip() or "新品种"
        description = self.name_page.desc_edit.text().strip()
        url = self.url_page.url_edit.text().strip()

        field_mapping = self.confirm_page.collect_mapping()
        return Product(
            name=name,
            description=description,
            target_url=url,
            field_mapping=[FieldMapping.from_dict(m) for m in field_mapping],
            context=self.url_page.context_config(),
        )


class _NamePage(QWizardPage):
    """第 1 步：命名。"""

    def __init__(self):
        super().__init__()
        self.setTitle("1️⃣ 命名品种")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("品种名称（如：太保洛阳学平险_基础）"))
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("太保洛阳学平险_加强")
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel("描述（可选）"))
        self.desc_edit = QLineEdit()
        self.desc_edit.setPlaceholderText("用于说明该品种的用途")
        layout.addWidget(self.desc_edit)

        self.registerField("name*", self.name_edit)


class _AttrPage(QWizardPage):
    """第 2 步：选择需要的属性（来自属性库，支持现场管理属性）。"""

    def __init__(self, prop_def_library: PropertyDefLibrary):
        super().__init__()
        self.setTitle("2️⃣ 选择要用的属性")
        self._library = prop_def_library

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("勾选该品种需要填写的属性（属性库：内置 + 自定义）"))

        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget, 1)

        btn_row = QHBoxLayout()
        self.manage_btn = QPushButton("🏷️ 管理属性…")
        self.manage_btn.setToolTip("打开属性管理页面，创建/编辑属性对象后返回刷新")
        self.manage_btn.clicked.connect(self._on_manage)
        btn_row.addWidget(self.manage_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.reload()

    def reload(self, checked: List[str] | None = None) -> None:
        """按属性库当前内容重建列表，可保留已勾选项。"""
        checked = set(checked or self.selected_properties())
        self.list_widget.clear()
        for d in self._library.all_defs():
            label = f"{d.name}　[{d.source}]"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, d.name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if d.name in checked else Qt.Unchecked)
            self.list_widget.addItem(item)

    def _on_manage(self):
        """现场打开属性管理（对话框形态复用内部页面），关闭后刷新列表。"""
        from .property_manager_page import PropertyManagerDialog

        dlg = PropertyManagerDialog(self, library=self._library)
        dlg.exec_()
        self.reload()

    def selected_properties(self) -> List[str]:
        selected: List[str] = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.Checked:
                selected.append(item.data(Qt.UserRole))
        return selected


class _UrlPage(QWizardPage):
    """第 3 步：输入目标 URL（支持从二维码图片导入）。"""

    def __init__(self):
        super().__init__()
        self.setTitle("3️⃣ 目标页面 URL")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入要自动填写的网页表单地址"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://dwx.cpic.com.cn/cxNew/dwx/mobilemsbv2/")
        layout.addWidget(self.url_edit)

        # 微信 H5 场景：直接输网址会提示「活动不存在」，真实链接在二维码里
        self.qr_btn = QPushButton("📷 从二维码图片导入 URL")
        self.qr_btn.setToolTip("微信消息里的活动二维码：把图片保存成文件后在这里导入，自动提取链接")
        self.qr_btn.clicked.connect(self._on_import_qr)
        layout.addWidget(self.qr_btn)

        # 移动端 H5：桌面上下文会被服务端拒绝（返回空白页）
        self.mobile_check = QCheckBox("移动端 / 微信 H5（模拟手机浏览器打开）")
        self.mobile_check.setToolTip(
            "手机网页需要以移动端环境访问：勾选后扫描与填表会使用移动 UA、"
            "触摸模拟与 Referer。太保学平险投保页等 H5 必须勾选。")
        layout.addWidget(self.mobile_check)

        layout.addWidget(QLabel(
            "适用于「扫码 / 微信内打开」的 H5 页面：\n"
            "这类链接带活动参数，直接输网址会被提示「活动不存在或已下线 ERR-2021」，\n"
            "请从微信消息中把二维码图片保存到本地，再点上面的按钮导入。"))

        layout.addWidget(QLabel("（Mock 模式下不会真实打开页面，使用示例字段）"))
        self.registerField("url*", self.url_edit)

    def is_mobile(self) -> bool:
        """是否以移动端（手机浏览器）环境扫描/填写。"""
        return self.mobile_check.isChecked()

    def context_config(self) -> dict:
        """构造品种的 context 配置（移动端 H5 需要 UA/touch/Referer）。"""
        if not self.is_mobile():
            return {}
        from page.browser_context import origin_of
        url = self.url_edit.text().strip()
        cfg = {"mobile": True}
        origin = origin_of(url)
        if origin:
            cfg["referer"] = origin
        return cfg

    def _on_import_qr(self) -> None:
        """从二维码图片提取 URL 并填入输入框。"""
        from PyQt5.QtWidgets import QFileDialog
        from core.qr_decode import decode_qr_text, decode_qr_url

        path, _ = QFileDialog.getOpenFileName(
            self, "选择二维码图片", "",
            "图片 (*.png *.jpg *.jpeg *.bmp *.webp)")
        if not path:
            return

        try:
            url = decode_qr_url(path)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "解码失败", str(e))
            return

        if url:
            self.url_edit.setText(url)
            QMessageBox.information(
                self, "已导入",
                f"已从二维码提取链接（下一步将扫描该页面）：\n{url}")
            return

        text = decode_qr_text(path)
        if text:
            QMessageBox.warning(
                self, "内容不是网址",
                f"这张二维码的内容不是网址：\n{text[:200]}")
        else:
            QMessageBox.warning(
                self, "未识别到二维码",
                "未能从图片中识别出二维码。请确认图片清晰、包含完整二维码"
                "（可从微信重新保存原图再试）。")


class _ScanPage(QWizardPage):
    """第 4 步：扫描页面字段（只读展示）。"""

    def __init__(self, wizard: ProductWizard):
        super().__init__()
        self.wizard = wizard
        self.setTitle("4️⃣ 扫描页面字段")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("以下是从页面扫描到的输入框（含 locator / placeholder / label）"))

        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        layout.addWidget(self.text_view, 1)

    def show_fields(self, fields: List[dict]):
        self.text_view.clear()
        if not fields:
            # 扫描为空的常见原因与下一步指引（微信 H5 场景实测：
            # 缺活动参数时页面显示 ERR-2021，扫不到任何输入框）
            self.text_view.append("⚠️ 未扫描到任何输入框。可能原因：")
            self.text_view.append("  1) 页面是纯 JS 应用，需要更长渲染时间或登录态")
            self.text_view.append("  2) 页面需要参数（如微信二维码里的活动链接，"
                                  "直接输网址会提示「活动不存在或已下线」）")
            self.text_view.append("")
            self.text_view.append("→ 可返回上一步（第 3 步），用"
                                  "「📷 从二维码图片导入 URL」重新获取链接后再扫描")
            return
        for f in fields:
            self.text_view.append(
                f"{f['locator']}  |  placeholder={f.get('placeholder','')}  |  label={f.get('label','')}"
            )

    def initializePage(self):
        # 进入本页时触发扫描 + 匹配
        self.wizard.build_product()
        super().initializePage()


class _ConfirmPage(QWizardPage):
    """第 5 步：确认/修正字段映射。"""

    def __init__(self, wizard: ProductWizard):
        super().__init__()
        self.wizard = wizard
        self.setTitle("5️⃣ 确认字段映射")
        outer = QVBoxLayout(self)
        outer.addWidget(QLabel("确认每个属性对应的页面字段（可手动修正 locator）；"
                               "右侧勾选框 = 该字段是否需要「回读校验」"))

        self._grid_layout = QVBoxLayout()
        self._grid_layout.setSpacing(6)
        outer.addLayout(self._grid_layout)
        outer.addStretch()

        self._locator_edits: dict = {}    # property -> QLineEdit
        self._verify_checks: dict = {}    # property -> QCheckBox（是否回读校验）
        self._property_order: List[str] = []
        self._matched: dict = {}

    def set_mapping(self, properties: List[str], mapping_result: dict):
        """填充确认行：每个属性一行「属性名 + locator 编辑框 + 回读校验勾选」。"""
        # 清空旧行
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._locator_edits.clear()
        self._verify_checks.clear()
        self._property_order = properties
        self._matched = mapping_result

        for prop in properties:
            locator = mapping_result.get(prop) or ""
            row = QHBoxLayout()

            name_label = QLabel(prop)
            name_label.setMinimumWidth(140)
            row.addWidget(name_label)

            edit = QLineEdit(locator)
            edit.setPlaceholderText("如 input[name=\"xxx\"]，留空则该字段不自动填")
            row.addWidget(edit, 1)
            self._locator_edits[prop] = edit

            check = QCheckBox("回读校验")
            check.setChecked(True)
            check.setToolTip("填完读回页面值、与期望值比对（发现没填上/填错位）。\n"
                             "识别、级联、日期这类易错字段建议保持勾选；\n"
                             "纯直填的常规字段可取消，少一些复核噪音。\n"
                             "注意：取消只影响校验与复核呈现，不影响填写。")
            row.addWidget(check)
            self._verify_checks[prop] = check

            self._grid_layout.addLayout(row)

        if not properties:
            empty = QLabel("未选择属性，请返回第 2 步勾选")
            empty.setStyleSheet("color: gray;")
            self._grid_layout.addWidget(empty)

    def collect_mapping(self) -> List[dict]:
        """收集用户最终确认的映射（读取 locator 编辑框与「回读校验」勾选）。"""
        result = []
        for prop in self._property_order:
            edit = self._locator_edits.get(prop)
            locator = edit.text().strip() if edit else ""
            check = self._verify_checks.get(prop)
            result.append({
                "property": prop,
                "locator": locator,
                "required": bool(locator),   # 有 locator 才必填
                "verify_after_fill": bool(check.isChecked()) if check else True,
            })
        return result

    def validatePage(self) -> bool:
        """保存前的校验：至少有属性映射。"""
        if not self._property_order:
            QMessageBox.warning(self, "提示", "请先选择至少一个属性")
            return False
        return True