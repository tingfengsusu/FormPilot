"""网页版 DeepSeek 一次性登录工具。

用法：
    python tools/deepseek_web_login.py

打开浏览器窗口 → 在窗口里扫码/登录 chat.deepseek.com → 检测到输入框后
自动提示成功并退出。登录态持久化到 .deepseek_profile/，之后 FormPilot
的网页版 LLM 调用（llm.mode=web）即可全自动运行，无需再次登录。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llm.web_client import DeepSeekWebClient


def main() -> int:
    client = DeepSeekWebClient(headless=False)
    print(f"登录态目录：{client.user_data_dir}")
    print("正在打开浏览器…请在窗口中登录 DeepSeek（最长等待 5 分钟）")

    ok = client.login_interactive(timeout_seconds=300)
    if ok:
        print("✓ 登录成功，登录态已保存。之后网页版 LLM 调用将全自动运行。")
        client.close()
        return 0

    print("✗ 超时未检测到登录。可重新运行本工具再试。")
    client.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
