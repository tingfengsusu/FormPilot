"""全局保险员信息源（手动配置，本地持久化）。

保险员（业务员）信息是全局稳定字段，不需要 OCR，由用户手动配置一次后
自动填充到所有品种。

持久化：保存在 config/global_user_info.yaml（含个人信息，已加入
.gitignore，不入版本库）。启动时自动加载，点「🚀 开始录入」时自动保存。
"""
from __future__ import annotations

import os
from typing import Any, Dict

import yaml

from core.property_source import PropertySource

# 项目根目录（sources 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_PATH = os.path.join(_PROJECT_ROOT, "config", "global_user_info.yaml")


class GlobalUserInfo(PropertySource):
    """全局：保险员信息（手动配置 + 本地持久化）。"""

    name = "保险员信息"

    #: 手动配置型来源：不提供文件上传入口
    accepts_file_input = False

    def __init__(self, path: str | None = None):
        self._path = path or _DEFAULT_PATH
        self._data: Dict[str, Any] = {}
        self.load()

    def get_fields(self) -> list[str]:
        return ["保险员姓名", "保险员工号", "保险员电话", "所属机构", "业务代码"]

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def load(self) -> None:
        """从 yaml 加载已保存的保险员信息（文件不存在时静默跳过）。"""
        if not os.path.exists(self._path):
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception:  # noqa: BLE001 — 配置损坏不应导致启动失败
            return
        if isinstance(data, dict):
            self._data.update(data)

    def save(self) -> str:
        """保存当前保险员信息到 yaml。

        :return: 保存的文件完整路径
        """
        with open(self._path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self._data, f, allow_unicode=True, sort_keys=False)
        return self._path

    # ------------------------------------------------------------------
    # PropertySource 接口
    # ------------------------------------------------------------------
    def set_data(self, data: Dict[str, Any]) -> None:
        """手动配置保险员信息。"""
        self._data.update(data)

    def extract(self, input_data: Any) -> Dict[str, Any]:
        # 全局信息源：input_data 为空或为一个 dict，直接返回已配置数据。
        if isinstance(input_data, dict):
            self._data.update(input_data)
        return dict(self._data)

    def is_input_ready(self) -> bool:
        return bool(self._data)
