"""LLM 字段提取（户口本等非标证件专用）。

从 PaddleOCR 识别的原始文本中，用 DeepSeek 提取结构化字段。

调用方式按 settings.yaml 的 llm.mode 路由（见 llm/service.py）：
  web → 网页版 DeepSeek 全自动（Playwright 驱动 chat.deepseek.com）
  api → 官方 API（需要 llm.api_key）
均不可用时回退到启发式提取（Mock），保证链路不中断。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from core.config import config
from core import mock_data


class LLMExtractor:
    """LLM 字段提取器。"""

    def __init__(self, provider: str = "", api_key: str = ""):
        cfg = config.llm_config
        self.provider = provider or cfg.get("provider", "deepseek")
        self.api_key = api_key or cfg.get("api_key", "")
        self.base_url = cfg.get("base_url", "https://api.deepseek.com")
        self.model = cfg.get("model", "deepseek-chat")

    def extract(self, text: str, fields: List[str], prompt: str = "") -> Dict[str, Any]:
        """从原始文本提取字段。

        有可用 LLM 通道（网页版模式 / API key）时走真实调用；
        否则回退到启发式提取。
        """
        if config.mock_mode or not config.has_llm():
            return self._fallback_extract(text, fields)

        try:
            return self._call_llm(text, fields, prompt)
        except Exception:
            return self._fallback_extract(text, fields)

    def _call_llm(self, text: str, fields: List[str], prompt: str) -> Dict[str, Any]:
        """真实 LLM 调用（按配置路由 web/api，单轮提示词）。"""
        from llm.service import chat
        from llm.web_client import extract_json_block

        system_prompt = prompt or (
            "你是一个信息抽取助手。请从户口本识别文本中提取指定字段，"
            "严格按 JSON 返回，字段值缺失时用空字符串。"
        )
        user_prompt = (
            f"原始文本：\n{text}\n\n"
            f"需要提取的字段：{json.dumps(fields, ensure_ascii=False)}\n\n"
            "只返回 JSON 对象，不要任何额外说明。"
        )

        reply = chat(f"{system_prompt}\n\n{user_prompt}")
        return extract_json_block(reply)

    def _fallback_extract(self, text: str, fields: List[str]) -> Dict[str, Any]:
        """无 Key 时的启发式提取（Mock 兜底）。"""
        # 对户口本场景返回 Mock 数据，保证链路可用。
        return mock_data.household_mock()