"""界面主题（gui/theme.py）：令牌展开、全局 QSS、关键按钮角色。

离屏跑（不开窗口）：QT_QPA_PLATFORM=offscreen。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QPushButton

from gui.main_window import MainWindow
from gui import theme
from gui.theme import TOKENS, apply_theme, mark, stylesheet


def test_stylesheet_expands_all_tokens():
    qss = stylesheet()

    assert "{{" not in qss and "}}" not in qss, "有没展开的令牌"
    for key in ("ACCENT", "TEXT", "BORDER", "SURFACE"):
        assert TOKENS[key] in qss, f"QSS 里缺少令牌 {key} 的值"
    assert 'QPushButton[role="primary"]' in qss
    assert 'QPushButton[role="danger"]' in qss
    assert 'QLabel[role="hint"]' in qss


def test_apply_theme_sets_fusion_and_global_stylesheet():
    app = QApplication.instance() or QApplication(sys.argv)
    qss = apply_theme(app)

    assert app.styleSheet() == qss
    assert len(qss) > 1000
    # 基准样式名要在 setStyleSheet 之前读（之后 app.style() 变成代理，读不到原名）；
    # 注意读 theme 模块属性而不是 from-import 的快照副本
    assert theme.LAST_BASE_STYLE.lower() == "fusion"


def test_mark_sets_role_property():
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    button = QPushButton("x")

    assert mark(button, "primary") is button
    assert button.property("role") == "primary"
    button.deleteLater()


def test_main_window_key_buttons_have_roles():
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()

    assert window.start_btn.property("role") == "primary"
    assert window.page_panel.fill_btn.property("role") == "primary"
    window.deleteLater()


if __name__ == "__main__":
    test_stylesheet_expands_all_tokens()
    test_apply_theme_sets_fusion_and_global_stylesheet()
    test_mark_sets_role_property()
    test_main_window_key_buttons_have_roles()
    print("test_theme.py 全部通过 ✓")
