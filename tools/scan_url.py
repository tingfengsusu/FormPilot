"""扫描任意 URL 的输入框（真实 Playwright），用于验证「定位功能」。

用法：
    python tools/scan_url.py <url> [--mobile]

    --mobile  以移动端/微信 H5 环境扫描（移动 UA + 触摸模拟 + Referer），
              移动端页面用桌面环境会被服务端拒绝（返回空文档）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as config_module

# 强制真实扫描（不受 settings.yaml 当前 mock_mode 影响）
config_module.config._data = {"mock_mode": False,
                              "playwright": {"headless": True}}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mobile = "--mobile" in sys.argv
    if not args:
        print(__doc__)
        return 2
    url = args[0]

    from page.scanner import Scanner

    context = {"mobile": True} if mobile else None
    try:
        # 直接调用真实扫描（不经过 Mock 回退，失败时如实报告）
        fields = Scanner().scan_or_raise(url, context)
    except Exception as e:  # noqa: BLE001
        print(f"扫描失败: {e}")
        return 1

    if not fields:
        print("未发现输入框（页面可能需要登录、有反爬限制或为纯 JS 延迟渲染）")
        return 1

    print(f"发现 {len(fields)} 个输入框：")
    for f in fields:
        print(f"  {f['locator']}"
              f"  | placeholder={f['placeholder']}"
              f" | label={f['label']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
