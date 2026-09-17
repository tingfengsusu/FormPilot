"""二维码解码：从图片中提取 URL（微信 H5 入口等场景）。

背景（实测）：太保投保页这类「微信内打开的 H5」，直接输网址会提示
    「活动不存在或已下线，请重新选择其他产品进行投保！ERR-2021」
—— 真正可用的链接带在二维码里（活动 ID / 机构代码等参数）。
用户只需把微信消息里的二维码图片保存成文件，本模块解码出真实 URL，
之后走正常的「扫描 → 匹配 → 确认 → 填表」流程，无需自动化微信本身。

依赖（二选一，均为可选依赖）：
  - pyzbar（轻量，优先）
  - opencv-python-headless（兜底）

已知限制：pyzbar 底层 zbar 对**非 ASCII 内容**（如中文）会做字符集推断
（可能按 Shift-JIS 解释），解码出的中文可能不准；URL 均为 ASCII，不受影响
——本项目只关心 URL，中文内容仅用于错误提示。
"""
from __future__ import annotations

import os
from typing import Optional


def decode_qr_text(image_path: str) -> Optional[str]:
    """解码图片中的第一个二维码，返回其文本内容；识别不到返回 None。"""
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"二维码图片不存在：{image_path}")

    text = _decode_with_pyzbar(image_path)
    if not text:
        text = _decode_with_opencv(image_path)
    return text


def decode_qr_url(image_path: str) -> Optional[str]:
    """解码并返回其中的 URL（http/https 开头）；内容不是网址则返回 None。"""
    text = decode_qr_text(image_path)
    if text and text.strip().lower().startswith(("http://", "https://")):
        return text.strip()
    return None


def _decode_with_pyzbar(image_path: str) -> Optional[str]:
    try:
        from pyzbar.pyzbar import decode as zbar_decode
        from PIL import Image
    except ImportError:
        return None
    try:
        results = zbar_decode(Image.open(image_path))
    except Exception:  # noqa: BLE001 — 图片格式/损坏等，交给下一个后端
        return None
    if not results:
        return None
    return results[0].data.decode("utf-8", errors="replace")


def _decode_with_opencv(image_path: str) -> Optional[str]:
    try:
        import cv2
    except ImportError:
        return None
    try:
        img = cv2.imread(image_path)
        if img is None:
            return None
        data, _points, _ = cv2.QRCodeDetector().detectAndDecode(img)
        return data or None
    except Exception:  # noqa: BLE001
        return None
