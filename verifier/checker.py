"""回读校验（页面值 vs 属性池值）。

Phase 4：实现真实回读（Playwright 页面的 input_value），
同时保留 verify_snapshot 便于 Mock 测试。
"""
from __future__ import annotations

from typing import Any, List

from core.product import Product
from core.property_pool import PropertyPool


class Checker:
    """回读校验器。"""

    @staticmethod
    def _is_blank(value: Any) -> bool:
        """属性池值为 None 或空串时跳过校验（视为「未填写，不比对」）。"""
        return value is None or str(value).strip() == ""

    def verify(self, page: Any, product: Product, pool: PropertyPool) -> List[dict]:
        """回读页面值，与属性池值比对，返回不一致项列表。

        :param page: Playwright 的 page 对象（有 input_value(locator) 方法）

        元素类型不同，取值方式也不同（实测踩过：日期选择器/学校框是
        ``div``/``span``，直接 ``input_value()`` 会抛「Node is not an
        <input>」）：

        - ``select``：input_value() 返回 option 的 value 而非可见文本，
          所以额外读选中项文本，任一匹配即通过
        - ``input`` / ``textarea``：读 ``value``
        - 其他（div/span 模拟的输入框）：读可见文本
        """
        issues: List[dict] = []
        for mapping in product.field_mapping:
            if not mapping.verify_after_fill:
                continue
            expected = pool.get(mapping.property)
            if self._is_blank(expected):
                continue
            expected_str = str(expected)
            try:
                el = page.locator(mapping.locator)
                tag = el.evaluate("e => e.tagName.toLowerCase()")
                if tag == "select":
                    # select：同时取 value 与选中项文本，任一匹配即通过
                    value_attr = el.input_value() or ""
                    text = el.evaluate(
                        "e => { const o = e.selectedOptions[0];"
                        "       return o ? (o.textContent || '').trim() : ''; }")
                    if expected_str not in (value_attr, text):
                        issues.append({
                            "property": mapping.property,
                            "expected": expected_str,
                            "actual": f"value={value_attr} / text={text}",
                            "locator": mapping.locator,
                        })
                    continue
                if tag in ("input", "textarea"):
                    page_value = el.input_value()
                else:
                    page_value = el.evaluate(
                        "e => (e.innerText || e.textContent || '').trim()")
            except Exception as e:
                # 元素读不到值（可能被隐藏/禁用），记为校验异常
                issues.append({
                    "property": mapping.property,
                    "expected": expected_str,
                    "actual": f"<读取失败: {e}>",
                    "locator": mapping.locator,
                })
                continue

            if str(page_value or "") != expected_str:
                issues.append({
                    "property": mapping.property,
                    "expected": expected_str,
                    "actual": page_value,
                    "locator": mapping.locator,
                })
        return issues

    def verify_snapshot(self, page_values: dict, product: Product, pool: PropertyPool) -> List[dict]:
        """用「页面值快照」做校验（Mock 模式用，便于无浏览器测试）。

        :param page_values: {locator: 页面实际值}
        """
        issues: List[dict] = []
        for mapping in product.field_mapping:
            if not mapping.verify_after_fill:
                continue
            actual = page_values.get(mapping.locator)
            expected = pool.get(mapping.property)
            if self._is_blank(expected):
                continue
            if actual != str(expected):
                issues.append({
                    "property": mapping.property,
                    "expected": str(expected),
                    "actual": actual,
                    "locator": mapping.locator,
                })
        return issues