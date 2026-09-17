"""在独立线程中运行 Playwright 同步 API 的辅助工具。

背景（实测）：Playwright 的 sync API 与线程绑定——**同一线程里只能存在一个
活动的 sync_playwright 实例**，第二个会直接报
「It looks like you are using Playwright Sync API inside the asyncio loop.」

本项目有两类使用者会落在主线程：
  - 网页版 DeepSeek 客户端（llm/web_client.py）：浏览器**常驻**，用完不关
  - DOM 扫描器（page/scanner.py）：临时开一次浏览器，用完即关

两者叠加时会互相冲突：用过网页版 LLM（如户口本识别）后再扫描页面，
扫描会失败并静默回退到 Mock 字段。解决办法是把**短任务**（扫描）放到
独立线程执行，与主线程的常驻客户端互不干扰；用完线程即退出。

（FormFiller 在 GUI 里本来就在 QThread 中运行，不受此影响。）
"""
from __future__ import annotations

import threading
from typing import Any, Callable


def run_in_thread(func: Callable, *args: Any, **kwargs: Any) -> Any:
    """在独立线程中执行 func 并返回结果；异常原样抛出。"""
    box: dict = {}

    def _target() -> None:
        try:
            box["value"] = func(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — 原样带回调用方
            box["error"] = e

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()

    if "error" in box:
        raise box["error"]
    return box.get("value")
