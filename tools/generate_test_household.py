"""生成测试用户口本图片（PIL 绘制，用于验证户口本识别链路）。

产物：tests/_samples/test_household.png
注意：合成图片，仅供本地链路测试。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def generate_test_household(output_dir: str = "tests/_samples") -> str:
    """绘制一页户口簿（常住人口登记卡风格），返回文件路径。"""
    os.makedirs(output_dir, exist_ok=True)
    out = os.path.join(output_dir, "test_household.png")

    W, H = 1000, 700
    img = Image.new("RGB", (W, H), (252, 250, 242))
    d = ImageDraw.Draw(img)
    d.rectangle([16, 16, W - 16, H - 16], outline=(140, 120, 90), width=3)

    d.text((W // 2 - 150, 40), "常住人口登记卡", font=_font(36), fill=(40, 30, 20))
    d.text((W - 330, 100), "测试样图（非真实证件）", font=_font(18), fill=(150, 60, 60))

    f22, f24 = _font(24), _font(26)

    rows = [
        ("户别", "家庭户口", "户主姓名", "王大力"),
        ("户号", "410300000000", "住址", "河南省洛阳市涧西区测试路1号"),
    ]
    y = 150
    for a1, v1, a2, v2 in rows:
        d.text((50, y), a1, font=f22, fill=(90, 70, 50))
        d.text((150, y), v1, font=f24, fill=(20, 16, 12))
        d.text((520, y), a2, font=f22, fill=(90, 70, 50))
        d.text((640, y), v2, font=f24, fill=(20, 16, 12))
        y += 60

    # 成员表格
    y += 10
    d.line([(40, y), (W - 40, y)], fill=(140, 120, 90), width=2)
    d.text((50, y + 12), "姓名", font=f22, fill=(90, 70, 50))
    d.text((280, y + 12), "与户主关系", font=f22, fill=(90, 70, 50))
    d.text((560, y + 12), "公民身份号码", font=f22, fill=(90, 70, 50))
    y += 56
    d.line([(40, y), (W - 40, y)], fill=(140, 120, 90), width=2)

    members = [
        ("王大力", "户主", "410300198001011230"),
        ("李秀英", "配偶", "410300198203153346"),
        ("王小力", "之子", "410305201509011235"),
    ]
    for name, rel, idnum in members:
        y += 16
        d.text((50, y), name, font=f24, fill=(20, 16, 12))
        d.text((280, y), rel, font=f24, fill=(20, 16, 12))
        d.text((560, y), idnum, font=f24, fill=(20, 16, 12))
        y += 44
        d.line([(40, y), (W - 40, y)], fill=(200, 190, 170), width=1)

    img.save(out)
    print(f"已生成测试户口本图片: {out}")
    return out


if __name__ == "__main__":
    generate_test_household()
