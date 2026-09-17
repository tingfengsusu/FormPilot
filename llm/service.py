"""LLM 统一调用入口：按配置选择「网页版（全自动）」或「官方 API」。

模式（settings.yaml → llm.mode）：
    web  网页版 DeepSeek：Playwright 驱动 chat.deepseek.com 全自动问答，
         无需 API key（首次需扫码登录一次，登录态持久化）
    api  官方 API：OpenAI 兼容接口，需要 llm.api_key

降级链：web 失败 → 若配置了 api_key 则回退 API → 仍失败则抛 LLMError
（调用方各自再有启发式 / 规则兜底）。
"""
from __future__ import annotations

from typing import List, Optional

from core.config import config


class LLMError(RuntimeError):
    """LLM 调用失败（所有模式均不可用）。"""


def api_chat(prompt: str) -> str:
    """官方 API 调用（OpenAI 兼容，惰性导入 SDK）。"""
    from openai import OpenAI

    cfg = config.llm_config
    client = OpenAI(api_key=cfg.get("api_key"), base_url=cfg.get("base_url"))
    resp = client.chat.completions.create(
        model=cfg.get("model", "deepseek-chat"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    return resp.choices[0].message.content or ""


def web_chat(prompt: str, images: Optional[List[str]] = None,
             timeout: Optional[float] = None) -> str:
    """网页版全自动调用（浏览器常驻单例）。"""
    from .web_client import get_web_client

    return get_web_client().ask(prompt, images=images, timeout=timeout)


def chat(prompt: str, images: Optional[List[str]] = None,
         timeout: Optional[float] = None) -> str:
    """统一入口：按配置路由到 web / api，并处理降级。"""
    mode = config.llm_mode

    if mode == "web":
        try:
            return web_chat(prompt, images=images, timeout=timeout)
        except Exception as web_err:  # noqa: BLE001
            if config.has_llm_key():
                try:
                    return api_chat(prompt)
                except Exception:  # noqa: BLE001 — 保留 web 的原始错误
                    raise LLMError(f"网页版与 API 均失败：{web_err}") from web_err
            raise LLMError(f"网页版调用失败：{web_err}") from web_err

    if not config.has_llm_key():
        raise LLMError(
            "未配置 LLM：请在 config/settings.yaml 设置 llm.mode=web（网页版全自动，"
            "先运行 python tools/deepseek_web_login.py 扫码登录一次）"
            "或填写 llm.api_key")
    return api_chat(prompt)


__all__ = ["LLMError", "chat", "api_chat", "web_chat"]
