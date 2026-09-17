"""LLM 调用层：网页版 DeepSeek（全自动）与官方 API 的统一入口。"""
from .service import LLMError, chat, api_chat
from .web_client import DeepSeekWebClient, DeepSeekWebError, NotLoggedInError

__all__ = [
    "LLMError", "chat", "api_chat",
    "DeepSeekWebClient", "DeepSeekWebError", "NotLoggedInError",
]
