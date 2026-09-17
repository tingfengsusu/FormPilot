"""生成测试用身份证图片（PIL 绘制，用于验证 OCR 链路）。

产物：tests/_samples/test_idcard.png
注意：这是合成图片，仅供本地链路测试；真实云 OCR 对合成图的识别
结果可能不完整（部分字段缺失属正常现象）。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

#: 测试数据（身份证号校验位按 GB 11643 计算，格式合法）
_TEST_ID = "410305201509011235"

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    r"C:\Windows\Fonts\simsun.ttc",    # 宋体
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_test_idcard(output_dir: str = "tests/_samples") -> str:
    """绘制一张二代身份证（正面）风格的测试图，返回文件路径。"""
    os.makedirs(output_dir, exist_ok=True)
    out = os.path.join(output_dir, "test_idcard.png")

    W, H = 1024, 640
    img = Image.new("RGB", (W, H), (214, 226, 240))
    d = ImageDraw.Draw(img)

    # 卡面渐变感（顶亮底暗）
    for y in range(H):
        shade = int(240 - 36 * y / H)
        d.line([(0, y), (W, y)], fill=(shade - 20, shade - 8, shade))

    d.rectangle([18, 18, W - 18, H - 18], outline=(150, 170, 200), width=3)

    d.text((64, 52), "中华人民共和国", font=_font(30), fill=(40, 60, 110))
    d.text((64, 92), "居民身份证", font=_font(30), fill=(40, 60, 110))
    d.text((W - 300, 60), "测试样图（非真实证件）", font=_font(18), fill=(150, 60, 60))

    f22 = _font(28)
    f26 = _font(32)

    d.text((64, 190), "姓    名", font=f22, fill=(60, 60, 60))
    d.text((260, 186), "张伟", font=f26, fill=(20, 20, 20))

    d.text((64, 250), "性    别", font=f22, fill=(60, 60, 60))
    d.text((260, 246), "男", font=f26, fill=(20, 20, 20))
    d.text((430, 250), "民    族", font=f22, fill=(60, 60, 60))
    d.text((600, 246), "汉", font=f26, fill=(20, 20, 20))

    d.text((64, 310), "出    生", font=f22, fill=(60, 60, 60))
    d.text((260, 306), "2015 年 9 月 1 日", font=f26, fill=(20, 20, 20))

    d.text((64, 370), "住    址", font=f22, fill=(60, 60, 60))
    d.text((260, 366), "河南省洛阳市涧西区测试路 1 号", font=f22, fill=(20, 20, 20))

    d.text((64, 470), "公民身份号码", font=f22, fill=(60, 60, 60))
    d.text((300, 464), _TEST_ID, font=_font(38), fill=(20, 20, 20))

    # 右侧头像占位
    d.rectangle([W - 260, 170, W - 80, 420], fill=(190, 205, 225),
                outline=(150, 170, 200), width=2)
    d.text((W - 232, 280), "头像占位", font=_font(20), fill=(120, 140, 170))

    img.save(out)
    print(f"已生成测试身份证图片: {out}")
    return out


if __name__ == "__main__":
    generate_test_idcard()
