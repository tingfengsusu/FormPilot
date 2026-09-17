"""配置加载器 — 读取 config/settings.yaml。

提供统一的配置访问入口，OCR 引擎、LLM、Playwright 等模块都从这里取值。
"""
from __future__ import annotations

import os
from typing import Any, Optional

import yaml

# 项目根目录（config 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_SETTINGS_PATH = os.path.join(_PROJECT_ROOT, "config", "settings.yaml")


class Config:
    """全局配置对象（惰性加载）。"""

    def __init__(self, path: Optional[str] = None):
        self._path = path or _DEFAULT_SETTINGS_PATH
        self._data: Optional[dict] = None

    def load(self) -> dict:
        """读取 settings.yaml（带缓存）。文件不存在时返回空配置。"""
        if self._data is not None:
            return self._data
        if not os.path.exists(self._path):
            self._data = {}
            return self._data
        with open(self._path, "r", encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}
        return self._data

    def get(self, dotted_path: str, default: Any = None) -> Any:
        """按点分路径读取配置，如 `get("ocr.tencent.secret_id")`。"""
        data = self.load()
        node = data
        for key in dotted_path.split("."):
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    @property
    def mock_mode(self) -> bool:
        """是否处于 Mock 模式（调试用，无需任何 API key）。"""
        return bool(self.get("mock_mode", False))

    @property
    def tencent_config(self) -> dict:
        """腾讯云 OCR 配置。"""
        return self.get("ocr.tencent", {}) or {}

    @property
    def llm_config(self) -> dict:
        """LLM 配置。"""
        return self.get("llm", {}) or {}

    @property
    def llm_mode(self) -> str:
        """LLM 调用模式：web（网页版全自动，无需 key）| api（官方 API）。"""
        return str(self.get("llm.mode", "api") or "api").lower()

    @property
    def llm_web_config(self) -> dict:
        """网页版 DeepSeek 配置（headless / user_data_dir / 超时）。"""
        return self.get("llm.web", {}) or {}

    def has_llm(self) -> bool:
        """是否有可用的 LLM 通道（网页版模式或已配置 API key）。"""
        return self.llm_mode == "web" or self.has_llm_key()

    def has_tencent_credentials(self) -> bool:
        """是否已配置腾讯云密钥。"""
        cfg = self.tencent_config
        return bool(cfg.get("secret_id") and cfg.get("secret_key"))

    def has_llm_key(self) -> bool:
        """是否已配置 LLM Key。"""
        return bool((self.llm_config or {}).get("api_key"))


#: 全局默认配置实例。
config = Config()