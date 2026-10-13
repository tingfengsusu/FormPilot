"""界面主题：设计令牌 + 全局 QSS（一处定义，全应用生效）。

为什么要有这一层：以前样式散在各处（7 处 inline ``setStyleSheet``），
间距、字号、颜色没有统一口径，界面像是"拼"出来的。这里把令牌集中，
QSS 一次性 apply；需要强调的控件用 :func:`mark` 打角色，由 QSS 按角色上色。

用法（``main.py``）::

    from gui.theme import apply_theme
    apply_theme(app)

角色（``mark(widget, role)``）：

- ``primary``  主操作（开始录入 / 批量录入 / 保存）
- ``danger``   破坏性操作（打回）
- ``hint``     提示文字（灰、小一号）

令牌改这里就够了 —— 想换配色只动 :data:`TOKENS`。
"""
from __future__ import annotations

import re
from typing import Any, Dict

#: 基准 Qt 样式（统一跨平台外观，让 QSS 颜色可预期）
_BASE_STYLE = "Fusion"

#: 最近一次 apply_theme 生效的基准样式名（测试用）
LAST_BASE_STYLE = ""

#: 设计令牌（浅色主题 + 单一强调色）
TOKENS: Dict[str, str] = {
    # 底色与表面
    "BG": "#f4f5f7",
    "SURFACE": "#ffffff",
    "HOVER": "#eef1f5",
    "PRESSED": "#e3e7ee",
    "HEADER": "#fafbfc",
    # 文字
    "TEXT": "#1f2328",
    "MUTED": "#6b7280",
    # 线条
    "BORDER": "#e2e5ea",
    "BORDER_STRONG": "#c7ccd4",
    # 强调色（蓝）
    "ACCENT": "#2563eb",
    "ACCENT_HOVER": "#1d4ed8",
    "ACCENT_PRESSED": "#1e40af",
    "ACCENT_DISABLED": "#a8c0f5",
    "ACCENT_LIGHT": "#e6edfd",
    # 危险色（打回等）
    "DANGER": "#b42318",
    "DANGER_BORDER": "#f1c2be",
    "DANGER_BG": "#fdecea",
    # 滚动条与提示
    "SCROLL": "#c9cfd8",
    "SCROLL_HOVER": "#aab2bd",
    "TOOLTIP_BG": "#2b2f36",
    # 字体
    "FONT_SIZE": "13",
}

_QSS = """
QWidget {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: {{FONT_SIZE}}px;
    color: {{TEXT}};
}
QMainWindow, QDialog, QWizard { background: {{BG}}; }

/* 工具栏与状态栏 */
QToolBar { background: {{SURFACE}}; border: 0; border-bottom: 1px solid {{BORDER}};
           padding: 6px 8px; spacing: 6px; }
QToolBar QToolButton { padding: 5px 10px; border-radius: 6px; }
QToolBar QToolButton:hover { background: {{HOVER}}; }
QStatusBar { background: {{SURFACE}}; border-top: 1px solid {{BORDER}}; color: {{MUTED}}; }

/* 按钮：默认（次级）与角色按钮 */
QPushButton {
    background: {{SURFACE}}; border: 1px solid {{BORDER}}; border-radius: 6px;
    padding: 5px 14px; min-height: 22px;
}
QPushButton:hover { background: {{HOVER}}; }
QPushButton:pressed { background: {{PRESSED}}; }
QPushButton:disabled { color: {{MUTED}}; background: {{BG}}; }
QPushButton[role="primary"] {
    background: {{ACCENT}}; border-color: {{ACCENT}}; color: #ffffff; font-weight: 600;
}
QPushButton[role="primary"]:hover { background: {{ACCENT_HOVER}}; border-color: {{ACCENT_HOVER}}; }
QPushButton[role="primary"]:pressed { background: {{ACCENT_PRESSED}}; border-color: {{ACCENT_PRESSED}}; }
QPushButton[role="primary"]:disabled { background: {{ACCENT_DISABLED}}; border-color: {{ACCENT_DISABLED}}; }
QPushButton[role="danger"] { color: {{DANGER}}; border-color: {{DANGER_BORDER}}; }
QPushButton[role="danger"]:hover { background: {{DANGER_BG}}; }

/* 输入类 */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {
    background: {{SURFACE}}; border: 1px solid {{BORDER}}; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {{ACCENT}}; selection-color: #ffffff;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
QTextEdit:focus, QPlainTextEdit:focus { border-color: {{ACCENT}}; }
QComboBox::drop-down { border: 0; width: 18px; }
QComboBox QAbstractItemView {
    background: {{SURFACE}}; border: 1px solid {{BORDER}}; outline: 0;
    selection-background-color: {{ACCENT_LIGHT}}; selection-color: {{TEXT}};
}

/* 分组框 */
QGroupBox {
    background: {{SURFACE}}; border: 1px solid {{BORDER}}; border-radius: 8px;
    margin-top: 10px; padding: 10px 8px 8px 8px;
}
QGroupBox::title {
    subcontrol-origin: margin; left: 10px; padding: 0 4px;
    color: {{MUTED}}; font-weight: 600;
}

/* 标签页 */
QTabWidget::pane { background: {{SURFACE}}; border: 1px solid {{BORDER}}; border-radius: 8px; }
QTabBar::tab { padding: 6px 14px; margin-right: 2px; color: {{MUTED}};
               border-top-left-radius: 6px; border-top-right-radius: 6px; }
QTabBar::tab:selected { color: {{ACCENT}}; background: {{SURFACE}};
                        border: 1px solid {{BORDER}}; border-bottom-color: {{SURFACE}};
                        font-weight: 600; }

/* 向导（新建品种）：页面白底 + 标题规格化，避免看着还是系统默认灰 */
QWizard { background: {{BG}}; }
QWizardPage { background: {{SURFACE}}; }
QLabel#qt_wizard_title { font-size: 16px; font-weight: 600; color: {{TEXT}}; }
QLabel#qt_wizard_subtitle { color: {{MUTED}}; }

/* 表格 */
QTableWidget, QTableView {
    background: {{SURFACE}}; border: 1px solid {{BORDER}}; border-radius: 8px;
    gridline-color: {{BORDER}}; selection-background-color: {{ACCENT_LIGHT}};
    selection-color: {{TEXT}};
}
QHeaderView::section { background: {{HEADER}}; border: 0; border-bottom: 1px solid {{BORDER}};
                       padding: 6px 8px; color: {{MUTED}}; font-weight: 600; }
QTableCornerButton::section { background: {{HEADER}}; border: 0; }

/* 列表 */
QListWidget, QListView { background: {{SURFACE}}; border: 1px solid {{BORDER}};
                         border-radius: 8px; outline: 0; }
QListWidget::item { padding: 6px 8px; border-radius: 4px; }
QListWidget::item:hover { background: {{HOVER}}; }
QListWidget::item:selected { background: {{ACCENT_LIGHT}}; color: {{TEXT}}; }

/* 滚动条（细，不抢视线；不自定义 indicator，保留系统勾选标记） */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: {{SCROLL}}; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: {{SCROLL_HOVER}}; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: {{SCROLL}}; border-radius: 5px; min-width: 24px; }
QScrollBar::handle:horizontal:hover { background: {{SCROLL_HOVER}}; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* 分隔条与提示 */
QSplitter::handle { background: {{BORDER}}; }
QSplitter::handle:hover { background: {{ACCENT}}; }
QToolTip { background: {{TOOLTIP_BG}}; color: #ffffff; border: 0; padding: 4px 8px; }

/* 提示角色：灰、小一号（替代散落的 inline setStyleSheet） */
QLabel[role="hint"] { color: {{MUTED}}; font-size: 12px; }
QLabel[role="title"] { font-weight: 600; font-size: 15px; }
"""


def stylesheet() -> str:
    """展开令牌后的 QSS（给测试与 ``setStyleSheet`` 用）。"""
    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        return TOKENS.get(key, match.group(0))

    return re.sub(r"\{\{(\w+)\}\}", _sub, _QSS)


def mark(widget: Any, role: str) -> Any:
    """给控件打角色（primary / danger / hint / title），由 QSS 上色。

    返回控件本身，方便链式写法。
    """
    widget.setProperty("role", role)
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    return widget


def apply_theme(app: Any) -> str:
    """应用主题：Fusion 基准样式 + 全局 QSS。返回 QSS 文本（便于测试）。

    基准样式名记录在 :data:`LAST_BASE_STYLE` —— 必须在 ``setStyleSheet`` **之前**读：
    设置全局样式表之后 ``app.style()`` 会被换成 QStyleSheetStyle 代理，读不到原名了。
    """
    global LAST_BASE_STYLE
    try:
        app.setStyle(_BASE_STYLE)      # 统一基准，避免系统原生风格吃掉 QSS 颜色
    except Exception:  # noqa: BLE001 — 样式名不可用时也要能跑
        pass
    LAST_BASE_STYLE = str(app.style().objectName() or "")
    qss = stylesheet()
    app.setStyleSheet(qss)
    return qss


__all__ = ["TOKENS", "LAST_BASE_STYLE", "apply_theme", "mark", "stylesheet"]
