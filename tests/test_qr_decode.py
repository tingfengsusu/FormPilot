"""二维码解码测试（微信 H5 活动链接场景）。

覆盖：
  - URL 二维码：解码结果与原始 URL 一致（含参数）
  - 非 URL 内容：decode_qr_url 返回 None，decode_qr_text 返回原文
  - 无二维码图片：两者都返回 None
  - 文件不存在：抛 FileNotFoundError
依赖缺失（qrcode 生成 / pyzbar 解码）时自动跳过。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test

from core.qr_decode import decode_qr_text, decode_qr_url


def _make_qr(content: str, path: str) -> bool:
    """生成二维码图片；缺 qrcode 库时返回 False。"""
    try:
        import qrcode
    except ImportError:
        return False
    qrcode.make(content).save(path)
    return True


def _decoder_available() -> bool:
    """pyzbar 或 opencv 任一可用即可。"""
    try:
        import pyzbar  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 — pyzbar 在部分系统缺 DLL
        pass
    try:
        import cv2  # noqa: F401
        return True
    except ImportError:
        return False


def test_decode_url_qrcode(tmp_path):
    """带活动参数的 URL 二维码（太保微信入口的真实形态）。"""
    if not _decoder_available():
        skip_test("未安装 pyzbar 或 opencv（pip install pyzbar）")
        return

    url = ("https://dwx.cpic.com.cn/cxNew/dwx/mobilemsbv2/"
           "?activityId=ACT2026&orgCode=LY001&channel=wechat")
    img = os.path.join(str(tmp_path), "qr_url.png")
    if not _make_qr(url, img):
        skip_test("未安装 qrcode（pip install qrcode pillow）")
        return

    assert decode_qr_url(img) == url
    assert decode_qr_text(img) == url
    print("✓ 二维码解码：带参数的 URL 提取正确")


def test_decode_non_url_qrcode(tmp_path):
    """非 URL 内容：不当作链接（ASCII 内容可精确校验）。"""
    if not _decoder_available():
        skip_test("未安装 pyzbar 或 opencv")
        return

    img = os.path.join(str(tmp_path), "qr_text.png")
    if not _make_qr("ORDER-123456789", img):
        skip_test("未安装 qrcode")
        return

    assert decode_qr_url(img) is None
    assert decode_qr_text(img) == "ORDER-123456789"


def test_decode_chinese_qrcode_not_treated_as_url(tmp_path):
    """中文内容的二维码：不误判为 URL。

    注：zbar 对非 ASCII 内容会做字符集推断（可能按 Shift-JIS 解释），
    中文文本不保证原样还原——本项目只用 URL（ASCII），故此处只断言
    「不被当作链接」这一关键行为。
    """
    if not _decoder_available():
        skip_test("未安装 pyzbar 或 opencv")
        return

    img = os.path.join(str(tmp_path), "qr_cn.png")
    if not _make_qr("保单编号 123456789", img):
        skip_test("未安装 qrcode")
        return

    assert decode_qr_url(img) is None
    assert decode_qr_text(img)  # 能解出内容即可（不校验中文是否原样）


def test_decode_no_qrcode_in_image(tmp_path):
    """普通图片（无二维码）：两个接口都返回 None。"""
    if not _decoder_available():
        skip_test("未安装 pyzbar 或 opencv")
        return

    from PIL import Image
    img = os.path.join(str(tmp_path), "plain.png")
    Image.new("RGB", (200, 120), (200, 210, 230)).save(img)

    assert decode_qr_text(img) is None
    assert decode_qr_url(img) is None


def test_decode_missing_file(tmp_path):
    """文件不存在：明确抛错（调用方据此提示用户）。"""
    try:
        decode_qr_text(os.path.join(str(tmp_path), "nope.png"))
        raise AssertionError("不存在的文件应抛 FileNotFoundError")
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_decode_url_qrcode(td)
        test_decode_non_url_qrcode(td)
        test_decode_chinese_qrcode_not_treated_as_url(td)
        test_decode_no_qrcode_in_image(td)
        test_decode_missing_file(td)
    print("test_qr_decode.py 全部通过 ✓")
