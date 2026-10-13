"""右下：页面预览与填表。"""
from __future__ import annotations

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QLabel, QTextEdit,
                             QPushButton)

from core.property_pool import PropertyPool
from core.product import Product
from core.config import config
from page.filler import FormFiller
from .theme import mark


class _FillWorker(QThread):
    """后台填表线程：在独立线程执行 Playwright 填表，完成后回传报告。"""

    finished_report = pyqtSignal(object, object)  # (report, issues)
    failed = pyqtSignal(str)

    def __init__(self, filler: FormFiller, headless: bool, parent=None):
        super().__init__(parent)
        self._filler = filler
        self._headless = headless

    def run(self):
        try:
            report = self._filler.fill(headless=self._headless)
            issues = getattr(self._filler, "_last_issues", [])
            self.finished_report.emit(report, issues)
        except Exception as e:
            self.failed.emit(str(e))


class PagePanel(QWidget):
    """右下：页面预览与填表（以文本区呈现填充报告 + 回读校验结果）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("🖥️ 页面预览与填表"))

        self.report_view = QTextEdit()
        self.report_view.setReadOnly(True)
        self.report_view.setPlaceholderText("填表结果将显示在这里……")
        layout.addWidget(self.report_view, 1)

        self.fill_btn = QPushButton("🚀 开始录入")
        mark(self.fill_btn, "primary")
        layout.addWidget(self.fill_btn)

        self._product: Product | None = None
        self._pool = PropertyPool()
        self._worker: _FillWorker | None = None

    def set_product(self, product: Product):
        self._product = product
        self._pool.clear()
        self.report_view.clear()
        self.report_view.append(f"已选择品种：{product.name}")
        self.report_view.append(f"目标地址：{product.target_url}")
        self.report_view.append(f"待填写字段：{len(product.field_mapping)} 项")

    def feed_input(self, values: dict):
        """由主窗口在用户提供输入后喂入属性池。"""
        self._pool.feed_dict(values)

    def run_fill(self):
        """启动后台线程执行填表（不阻塞 GUI）。"""
        if self._product is None:
            self.report_view.append("⚠️ 尚未选择品种")
            return

        # 若已有填表线程在跑，先忽略
        if self._worker is not None and self._worker.isRunning():
            return

        filler = FormFiller(
            self._product, self._pool,
            # 从 settings.yaml 读取，不再硬编码；hold 秒数也传下去，
            # 避免默认 60 秒后浏览器被自动关闭。
            browser_hold_seconds=int(
                config.get("playwright.browser_hold_seconds", 300)),
        )
        self.report_view.clear()
        self.report_view.append(f"品种：{self._product.name}")
        if config.mock_mode or not FormFiller._playwright_available():
            self.report_view.append("—— 填表报告（Mock 模式）——")
        else:
            self.report_view.append("—— 正在后台启动浏览器填表……——")

        self.fill_btn.setEnabled(False)
        self.fill_btn.setText("⏳ 录入中……")

        headless = bool(config.get("playwright.headless", False))
        self._worker = _FillWorker(filler, headless=headless, parent=self)
        self._worker.finished_report.connect(self._on_fill_finished)
        self._worker.failed.connect(self._on_fill_failed)
        self._worker.start()

    def _on_fill_finished(self, report, issues):
        self.fill_btn.setEnabled(True)
        self.fill_btn.setText("🚀 开始录入")

        self.report_view.clear()
        self.report_view.append(f"品种：{self._product.name}")
        if config.mock_mode or not FormFiller._playwright_available():
            self.report_view.append("—— 填表报告（Mock 模式）——")
        else:
            self.report_view.append("—— 填表报告（Playwright 真实填表）——")

        for r in report:
            status = r["status"]
            if status == "filled":
                line = f"✓ {r['property']} → {r['locator']} = {r['value']}"
                if r.get("message"):
                    line += f"（{r['message']}）"
                self.report_view.append(line)
            elif status == "missing":
                self.report_view.append(f"✗ {r['message']}")
            elif status == "mismatch":
                self.report_view.append(f"⚠ {r['property']}：{r['message']}")
            elif status == "error":
                self.report_view.append(f"✗ {r['property']}：{r['message']}")
            else:
                self.report_view.append(f"- {r['property']}（{status}）")

        self.report_view.append("")
        if issues:
            self.report_view.append(f"⚠ 回读校验：{len(issues)} 个字段不一致")
            for i in issues:
                self.report_view.append(
                    f"  · {i['property']}: 期望「{i['expected']}」实际「{i['actual']}」")
        else:
            self.report_view.append("✓ 回读校验通过")

        self.report_view.append("")
        if config.mock_mode or not FormFiller._playwright_available():
            self.report_view.append("（Mock 模式：未真实调用浏览器）")
        else:
            self.report_view.append("（已真实填表，请在浏览器核对后手动提交）")

    def _on_fill_failed(self, err: str):
        self.fill_btn.setEnabled(True)
        self.fill_btn.setText("🚀 开始录入")
        self.report_view.append("")
        self.report_view.append(f"✗ 填表异常：{err}")