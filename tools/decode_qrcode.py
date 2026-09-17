"""从二维码图片提取 URL（微信消息里的活动二维码等）。

用法：
    python tools/decode_qrcode.py <图片路径>

场景：太保投保页这类「微信内打开」的 H5，直接输网址会提示
「活动不存在或已下线 ERR-2021」——真正可用的链接带在二维码里。
把微信里的二维码图片保存成文件，用本工具提取链接，然后：
  1. 粘贴到「+ 新建品种」第 3 步的 URL 输入框（或直接用向导里的
     「📷 从二维码图片导入 URL」按钮），或
  2. 更新已有品种 yaml 的 target_url
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.qr_decode import decode_qr_text, decode_qr_url


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    image_path = sys.argv[1]
    try:
        url = decode_qr_url(image_path)
    except FileNotFoundError as e:
        print(f"错误：{e}")
        return 1

    if url:
        print(url)
        return 0

    text = decode_qr_text(image_path)
    if text:
        print(f"二维码内容不是网址：{text[:300]}")
        return 1

    print("未能从图片中识别出二维码。"
          "请确认：图片清晰完整、包含整个二维码（可从微信重新保存原图）。"
          "\n提示：解码依赖 pyzbar 或 opencv，未安装时请执行 "
          "pip install pyzbar pillow")
    return 1


if __name__ == "__main__":
    sys.exit(main())
