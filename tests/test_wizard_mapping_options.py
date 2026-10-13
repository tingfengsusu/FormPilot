"""品种向导第 5 步的「回读校验」勾选框：勾选状态必须落进品种配置。

覆盖：
  - 默认全勾（保持既有行为：新字段默认回读校验）
  - 取消勾选 → collect_mapping 里 verify_after_fill = False
  - 该结果经 Product 往返（to_dict / from_dict）后不丢

离屏跑（不开窗口）：QT_QPA_PLATFORM=offscreen。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from core.product import FieldMapping, Product
from gui.product_wizard import ProductWizard, _ConfirmPage


def test_verify_checkbox_defaults_on_and_persists():
    app = QApplication.instance() or QApplication(sys.argv)
    wizard = ProductWizard()
    page = _ConfirmPage(wizard)

    properties = ["学生姓名", "备注"]
    page.set_mapping(properties, {"学生姓名": 'input[name="xm"]',
                                  "备注": 'input[name="bz"]'})

    # 默认全勾
    assert all(c.isChecked() for c in page._verify_checks.values())

    # 取消「备注」的校验
    page._verify_checks["备注"].setChecked(False)
    mapping = {m["property"]: m for m in page.collect_mapping()}

    assert mapping["学生姓名"]["verify_after_fill"] is True
    assert mapping["备注"]["verify_after_fill"] is False
    assert mapping["备注"]["required"] is True          # 有 locator 即必填（不受勾选影响）

    # 往返品种配置不丢
    product = Product(name="测试", field_mapping=[
        FieldMapping.from_dict(m) for m in page.collect_mapping()])
    again = Product.from_dict(product.to_dict())
    flags = {m.property: m.verify_after_fill for m in again.field_mapping}
    assert flags["学生姓名"] is True and flags["备注"] is False

    app.processEvents()
    wizard.deleteLater()


if __name__ == "__main__":
    test_verify_checkbox_defaults_on_and_persists()
    print("test_wizard_mapping_options.py 全部通过 ✓")
