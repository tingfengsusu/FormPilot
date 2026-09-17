"""LLM 字段匹配器（页面字段名 → 品种属性名）。

Phase 3：接入真实 LLM 调用（DeepSeek），未配置 / Mock 模式时回退到
启发式关键词匹配。语义鸿沟（"xm" vs "姓名"）是本文档 11.2 节核心亮点。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from core.config import config


class FieldMatcher:
    """把页面字段名（如 'xm'）映射到品种属性名（如 '学生姓名'）。"""

    _keyword_map = {
        "学生姓名": ["xm", "name", "姓名", "student"],
        "学生身份证号": ["sfzh", "idcard", "身份证", "sfz", "id"],
        "监护人姓名": ["jhrmc", "guardian", "监护人", "jhr"],
        "监护人电话": ["lxdh", "phone", "tel", "电话", "dh", "mobile"],
        "保险方案": ["baoxian", "plan", "方案", "bx", "scheme"],
        "保险员姓名": ["bxyxm", "agent", "保险员", "业务员"],
    }

    def match(self, page_fields: List[dict], product_fields: List[str]) -> Dict[str, Any]:
        """返回 {属性名: locator} 映射，不确定的设为 None。

        优先走 LLM（网页版全自动 / 官方 API），失败回退启发式匹配。
        """
        if not config.mock_mode and config.has_llm():
            try:
                return self.match_via_llm(page_fields, product_fields)
            except Exception:
                pass
        return self.match_heuristic(page_fields, product_fields)

    def match_via_llm(self, page_fields: List[dict], product_fields: List[str]) -> Dict[str, Any]:
        """真实 LLM 调用：构造 prompt，返回 JSON 映射。"""
        prompt = self._build_prompt(page_fields, product_fields)
        result = self._call_llm(prompt)
        # 校验：LLM 返回的 locator 必须在 page_fields 中，否则置 None
        valid_locators = {pf.get("locator") for pf in page_fields}
        cleaned: Dict[str, Any] = {}
        for field in product_fields:
            loc = result.get(field)
            cleaned[field] = loc if loc in valid_locators else None
        return cleaned

    def _build_prompt(self, page_fields: List[dict], product_fields: List[str]) -> str:
        return f"""你是网页表单字段匹配助手。

页面字段：
{json.dumps(page_fields, ensure_ascii=False, indent=2)}

目标属性：
{json.dumps(product_fields, ensure_ascii=False, indent=2)}

请判断每个目标属性对应哪个页面字段的 locator，输出纯 JSON 映射。
如果不确定，值设为 null。只输出 JSON，不要其他文字。"""

    def _call_llm(self, prompt: str) -> Dict[str, Any]:
        """调用 LLM 并解析 JSON 映射。

        按 settings.yaml 的 llm.mode 路由：
          web → 网页版 DeepSeek 全自动（Playwright 驱动，无需 API key）
          api → 官方 API（OpenAI 兼容）
        """
        from llm.service import chat
        from llm.web_client import extract_json_block

        return extract_json_block(chat(prompt))

    def match_heuristic(self, page_fields: List[dict], product_fields: List[str]) -> Dict[str, Any]:
        """启发式关键词匹配（LLM 回退）。"""
        result: Dict[str, Any] = {}
        for field in product_fields:
            result[field] = self._find_locator(field, page_fields)
        return result

    def _find_locator(self, property_name: str, page_fields: List[dict]) -> Any:
        keywords = self._keyword_map.get(property_name, [property_name])
        for pf in page_fields:
            haystack = (
                pf.get("locator", "")
                + " "
                + pf.get("placeholder", "")
                + " "
                + pf.get("label", "")
            ).lower()
            for kw in keywords:
                if kw.lower() in haystack:
                    return pf.get("locator")
        return None