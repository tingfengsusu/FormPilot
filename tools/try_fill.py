"""手动真实填表验证工具（对指定品种执行一次「填表 + 回读校验」）。

用法：
    python tools/try_fill.py "太保洛阳学平险_基础"

说明：
  - 使用测试数据填充；**不会点击提交按钮**（半自动设计：人工核对后自行提交）
  - 适合验证「新接入的真实页面」的定位与填写是否可用
  - 会真实打开浏览器并访问目标页面（注意生产页面的访问频率限制）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as config_module

#: 测试数据（品种属性名 → 值）；按需补充
_TEST_VALUES = {
    "学生姓名": "测试学生",
    "学生身份证号": "410305201509011235",
    "监护人姓名": "王大力",
    "监护人电话": "13800000000",
    "保险员姓名": "李经理",
    "保险员工号": "TPY0001",
    "保险员电话": "13900000000",
    "出发地": "洛阳",
    "到达地": "上海",
    "出发日期": "2026-10-01",
}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    product_name = sys.argv[1]

    from core.product_library import ProductLibrary
    lib = ProductLibrary()
    products = {p.name: p for p in lib.list_products()}
    product = products.get(product_name)
    if product is None:
        print(f"未找到品种「{product_name}」。可用：{list(products)}")
        return 1

    # 真实模式（不改动磁盘配置，仅本次进程内生效）
    config_module.config._data = {"mock_mode": False}

    from core.property_pool import PropertyPool
    from page.filler import FormFiller

    pool = PropertyPool()
    used = {k: v for k, v in _TEST_VALUES.items()
            if k in product.property_names()}
    pool.feed_dict(used)

    print(f"品种：{product.name}")
    print(f"目标：{product.target_url}")
    print(f"上下文：{product.context or '（桌面）'}")
    print(f"测试值：{used}")
    print("-" * 50)

    filler = FormFiller(product, pool, browser_hold_seconds=0)
    report = filler.fill(headless=True)

    for r in report:
        mark = {"filled": "✓", "missing": "✗", "skipped": "-", "error": "✗",
                "mismatch": "⚠"}.get(r["status"], "?")
        line = f"{mark} {r['property']}: {r.get('value') or ''}"
        if r.get("message"):
            line += f"（{r['message']}）"
        print(line)

    issues = getattr(filler, "_last_issues", [])
    browser_failed = bool(report) and report[0]["property"] == "(浏览器)"
    print("-" * 50)
    if browser_failed:
        print("✗ 未能完成填表（页面打开失败）：常见原因是目标站点限流——"
              "稍后重试，或更换新的活动链接")
        return 1
    print("回读校验：", "零差异 ✓" if not issues else f"{len(issues)} 项不一致")
    for i in issues:
        print(f"  · {i['property']}: 期望「{i['expected']}」实际「{i['actual']}」")
    print("\n（未点击提交按钮——请在浏览器中人工核对后自行提交）")
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
