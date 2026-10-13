"""FormPilot 启动入口。

运行方式：
    python main.py
"""
from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.theme import apply_theme


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())