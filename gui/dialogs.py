"""统一的确认弹窗（中文按钮）。

为什么单独放一个：Qt 的 ``QMessageBox.question`` 在没装中文翻译时按钮是英文
Yes/No（实测界面上就是中英混杂），这里统一成中文。
"""
from __future__ import annotations

from typing import Any

from PyQt5.QtWidgets import QMessageBox


def ask(parent: Any, title: str, text: str,
        yes: str = "确定", no: str = "取消") -> bool:
    """"是/否"确认框，按钮文案中文；返回是否点了"是"。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes_btn = box.addButton(yes, QMessageBox.YesRole)
    box.addButton(no, QMessageBox.NoRole)
    box.setDefaultButton(yes_btn)
    box.exec_()
    return box.clickedButton() is yes_btn


__all__ = ["ask"]
