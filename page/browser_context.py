"""浏览器上下文构造 —— 支持移动端 / 微信 H5 场景。

背景（实测）：太保投保页是移动端 H5，用默认桌面上下文访问会被拒
（返回 39 字节空文档）；使用「移动 UA + touch + Referer + zh-CN」
后正常渲染。品种 yaml 通过 context 字段声明所需环境：

    context:
      mobile: true                 # 模拟手机浏览器（UA + touch + 视口）
      wechat: true                 # 用微信内置浏览器 UA（MicroMessenger）
      referer: https://dwx.cpic.com.cn/   # 可选，默认取 target_url 的 origin
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

#: 通用移动端（iPhone Safari）
MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
             "Mobile/15E148 Safari/604.1")

#: 微信内置浏览器（MicroMessenger）
WECHAT_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
             "MicroMessenger/8.0.49(0x18003133) NetType/WIFI Language/zh_CN")

_MOBILE_VIEWPORT = {"width": 420, "height": 900}


def origin_of(url: str) -> str:
    """取 URL 的 origin（如 https://dwx.cpic.com.cn）。"""
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}" if p.scheme and p.netloc else ""
    except Exception:  # noqa: BLE001
        return ""


def create_context(browser, cfg: Optional[Dict[str, Any]] = None,
                   target_url: str = ""):
    """按品种 context 配置创建浏览器上下文（默认桌面）。

    :param cfg: 品种 yaml 的 context 段（可为 None）
    :param target_url: 用于推导默认 Referer
    """
    cfg = cfg or {}
    headers: Dict[str, str] = {}

    if not cfg.get("mobile"):
        return browser.new_context()

    ua = cfg.get("user_agent") or (WECHAT_UA if cfg.get("wechat") else MOBILE_UA)
    headers["Accept-Language"] = "zh-CN,zh;q=0.9"
    referer = cfg.get("referer") or origin_of(target_url)
    if referer:
        headers["Referer"] = referer

    return browser.new_context(
        user_agent=ua,
        viewport=_MOBILE_VIEWPORT,
        is_mobile=True,
        has_touch=True,
        locale="zh-CN",
        extra_http_headers=headers,
    )
