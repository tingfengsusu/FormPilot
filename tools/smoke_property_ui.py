"""属性管理 GUI 离屏烟雾测试（不开窗口，验证构造与交互逻辑）。

覆盖：属性管理内部页面（内联编辑/保存/删除/恢复默认）、主窗口页面切换、
品种向导属性列表、属性面板分组与下拉渲染、分组框图片预览、
行级上传按钮已移除、手动来源隐藏上传入口。

运行：QT_QPA_PLATFORM=offscreen python tools/smoke_property_ui.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage
from PyQt5.QtWidgets import QApplication, QMessageBox

from core.product import Product
from core.property_def import PropertyDef, PropertyDefLibrary, TYPE_SELECT


def main() -> None:
    app = QApplication(sys.argv)

    # 屏蔽模态弹窗，让按钮处理函数可以无头跑通
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
    QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)

    tmpdir = tempfile.mkdtemp()
    lib = PropertyDefLibrary(os.path.join(tmpdir, "property_library.yaml"))

    # ---- 1. 属性管理页：新增 + 内联编辑 + 保存 ----
    from gui.property_manager_page import PropertyManagerWidget

    page = PropertyManagerWidget(library=lib)
    assert page.table.rowCount() > 0, "应列出内置属性"

    page.add_row()
    row = page.table.rowCount() - 1
    page.table.item(row, 0).setText("就读年级")
    page.table.cellWidget(row, 1).setCurrentText("Excel名单")
    type_combo = page.table.cellWidget(row, 2)
    for i in range(type_combo.count()):
        if type_combo.itemData(i) == TYPE_SELECT:
            type_combo.setCurrentIndex(i)
            break
    page.table.item(row, 3).setText("一年级，二年级，三年级")   # 选项
    page.table.item(row, 4).setText("一年级")                  # 默认值
    page.table.item(row, 5).setCheckState(Qt.Checked)          # 必填
    page.table.item(row, 6).setText("烟雾测试新增")             # 描述
    page.save()

    saved = lib.get("就读年级")
    assert saved is not None and saved.options == ["一年级", "二年级", "三年级"]
    assert saved.default_value == "一年级" and saved.required
    assert saved.source == "Excel名单"

    # 非下拉类型的「选项」列不可编辑
    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == "学生姓名":
            item = page.table.item(r, 3)
            assert not (item.flags() & Qt.ItemIsEditable), "文本类型选项列应只读"
            assert item.text() == "——"
            break

    # 内置属性名只读
    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == "性别":
            assert not (page.table.item(r, 0).flags() & Qt.ItemIsEditable), \
                "内置属性名应只读"
            break

    # 校验：下拉类型必须有选项
    page.add_row()
    r2 = page.table.rowCount() - 1
    page.table.item(r2, 0).setText("无选项测试")
    tc = page.table.cellWidget(r2, 2)
    for i in range(tc.count()):
        if tc.itemData(i) == TYPE_SELECT:
            tc.setCurrentIndex(i)
            break
    try:
        page.collect_defs()
        raise AssertionError("下拉无选项应被拒绝")
    except ValueError:
        pass
    page.table.removeRow(r2)

    # 覆盖内置属性 → 保存 → 恢复默认
    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == "性别":
            page.table.item(r, 3).setText("男，女，其他")
            break
    page.save()
    assert lib.get("性别").options == ["男", "女", "其他"]
    assert not lib.is_builtin("性别")

    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == "性别":
            page.table.selectRow(r)
            break
    page.revert_selected()
    assert lib.get("性别").options == ["男", "女"], "恢复默认应撤销覆盖"
    assert lib.is_builtin("性别")

    # 删除自定义属性
    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == "就读年级":
            page.table.selectRow(r)
            break
    page.delete_selected()
    assert lib.get("就读年级") is None
    # 内置属性不可删除
    for r in range(page.table.rowCount()):
        if page.table.item(r, 0).text() == "学生姓名":
            page.table.selectRow(r)
            break
    page.delete_selected()
    assert lib.get("学生姓名") is not None, "内置属性不应被删除"

    # ---- 2. 主窗口：属性管理是内部页面切换 ----
    from gui.main_window import MainWindow

    win = MainWindow()
    assert win.stack.currentIndex() == 0
    win._on_manage_properties()
    assert win.stack.currentIndex() == 1, "应切换到属性管理页（程序内页面）"
    assert win.prop_mgmt_action.text() == "⬅ 返回主界面"
    win._on_manage_properties()
    assert win.stack.currentIndex() == 0, "再次点击应返回主界面"
    assert win.prop_mgmt_action.text() == "🏷️ 属性管理"

    # ---- 3. 品种向导第 2 步：属性列表来自属性库 ----
    lib.upsert(PropertyDef(name="就读年级", source="Excel名单",
                           field_type=TYPE_SELECT, options=["一年级", "二年级"]))
    from gui.product_wizard import ProductWizard

    wizard = ProductWizard(prop_def_library=lib)
    attr = wizard.attr_page
    labels = [attr.list_widget.item(i).data(Qt.UserRole)
              for i in range(attr.list_widget.count())]
    assert "就读年级" in labels, "向导属性列表应包含自定义属性"
    assert "学生姓名" in labels, "向导属性列表应包含内置属性"

    for i in range(attr.list_widget.count()):
        if attr.list_widget.item(i).data(Qt.UserRole) == "就读年级":
            attr.list_widget.item(i).setCheckState(Qt.Checked)
    attr.reload()
    assert "就读年级" in attr.selected_properties(), "reload 后勾选应保留"

    # ---- 4. 属性面板：按属性对象分组 + 下拉渲染 ----
    from gui.property_panel import PropertyPanel

    panel = PropertyPanel(prop_def_library=lib)
    product = Product.from_dict({
        "name": "测试",
        "field_mapping": [
            {"property": "就读年级", "locator": 'select[name="nj"]'},
            {"property": "学生姓名", "locator": 'input[name="xm"]'},
            {"property": "性别", "locator": 'select[name="xb"]'},
        ],
    })
    panel.show_for_product(product)
    assert "就读年级" in panel.source_groups["Excel名单"].field_widgets
    assert "学生姓名" in panel.source_groups["身份证OCR"].field_widgets

    from PyQt5.QtWidgets import QComboBox
    xb = panel.source_groups["身份证OCR"].field_widgets["性别"]
    assert isinstance(xb.value_edit, QComboBox), "性别应为下拉框"

    # ---- 5. 分组框：行级按钮已移除 + 图片预览 + 手动来源隐藏上传 ----
    from gui.widgets.property_field import PropertyField
    from gui.widgets.source_group import SourceGroup
    from sources.global_user_info import GlobalUserInfo
    from core.property_source import PropertySource

    assert not hasattr(PropertyField("x"), "upload_btn"), "属性行不应再有上传按钮"

    class _DummySource(PropertySource):
        name = "测试来源"

        def get_fields(self):
            return ["姓名"]

        def extract(self, input_data):
            return {"姓名": "测试学生"}

        def is_input_ready(self):
            return False

    img_path = os.path.join(tmpdir, "idcard.png")
    img = QImage(240, 150, QImage.Format_RGB32)
    img.fill(0x3366FF)
    assert img.save(img_path)

    group = SourceGroup("身份证OCR", ["姓名"], source=_DummySource())
    assert group.upload_btn.isVisibleTo(group), "分组底部应有上传入口"
    assert group.preview_label.pixmap() is None or group.preview_label.pixmap().isNull()
    group._process_input(img_path)
    assert group.field_widgets["姓名"].value() == "测试学生"
    pix = group.preview_label.pixmap()
    assert pix is not None and not pix.isNull(), "上传图片后应显示预览"
    assert group.preview_label.isVisibleTo(group), "预览应可见"

    manual = GlobalUserInfo(path=os.path.join(tmpdir, "g.yaml"))
    g2 = SourceGroup("保险员信息", ["保险员姓名"], source=manual)
    assert not g2.upload_btn.isVisibleTo(g2), "手动配置来源应隐藏上传入口"

    # ---- 6. 向导：从二维码图片导入 URL（微信 H5 场景） ----
    from PyQt5.QtWidgets import QFileDialog

    from tools.generate_test_idcard import generate_test_idcard  # noqa: F401（确保模块可导入）
    import qrcode

    qr_url = ("https://dwx.cpic.com.cn/cxNew/dwx/mobilemsbv2/"
              "?activityId=ACT2026&orgCode=LY001")
    qr_path = os.path.join(tmpdir, "entry_qr.png")
    qrcode.make(qr_url).save(qr_path)

    wizard2 = ProductWizard(prop_def_library=lib)
    url_page = wizard2.url_page
    old_dialog = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (qr_path, ""))
    try:
        url_page._on_import_qr()
    finally:
        QFileDialog.getOpenFileName = old_dialog
    assert url_page.url_edit.text() == qr_url, \
        f"应从二维码导入 URL: {url_page.url_edit.text()}"

    # 扫描为空时给出引导（而非空白页）
    wizard2.scan_page.show_fields([])
    hint = wizard2.scan_page.text_view.toPlainText()
    assert "未扫描到任何输入框" in hint and "二维码" in hint

    print("smoke_property_ui.py 全部通过 ✓")


if __name__ == "__main__":
    main()
