"""FormPilot 启动入口。

运行方式：
    python main.py
"""
from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication

from gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())