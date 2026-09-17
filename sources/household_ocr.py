"""户口本 OCR —— 双步策略 + 多模态快路径。

提取链路（按可用性自动选择）：
  1. **多模态快路径**（无本地 PaddleOCR 时优先）：
     直接把户口本照片发给网页版 DeepSeek（llm/web_client 支持图片附件），
     让多模态模型读图返回结构化 JSON —— 不需要安装 PaddleOCR（重依赖）
  2. **常规两步**（安装 PaddleOCR 后）：
     PaddleOCR 识别原始文本 → LLM 从文本提取结构化字段（见设计文档 6.1 节）
  3. 全部不可用时回退 Mock 数据，并在结果里写 `_fallback_reason`，
     保证「拖图 → 识别」链路不中断
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from core.config import config
from core.property_source import PropertySource
from ocr.paddle_ocr import PaddleOCR
from ocr.llm_extractor import LLMExtractor


class HouseholdOCR(PropertySource):
    """户口本 OCR 属性来源。"""

    name = "户口本OCR"

    def __init__(self) -> None:
        self._image_path: str | None = None
        self._last_result: Dict[str, Any] = {}

    def get_fields(self) -> list[str]:
        return ["户主姓名", "与户主关系", "户号", "成员1姓名", "成员1身份证号"]

    def extract(self, input_data: Any) -> Dict[str, Any]:
        image_path = input_data if isinstance(input_data, str) else str(input_data)
        self._image_path = image_path

        if PaddleOCR.is_installed():
            result = self._extract_two_step(image_path)
        else:
            # 本地 OCR 未安装：优先走多模态快路径
            result = self._extract_via_vision(image_path)
            if result is None:
                result = self._extract_two_step(image_path)
                result["_fallback_reason"] = (
                    "本地 PaddleOCR 未安装且网页版多模态提取失败，"
                    "当前结果基于模拟文本")

        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # 多模态快路径（网页版 DeepSeek 直发图片）
    # ------------------------------------------------------------------
    def _extract_via_vision(self, image_path: str) -> Optional[Dict[str, Any]]:
        """把照片直接发给多模态 LLM 提取字段；失败返回 None。"""
        if config.mock_mode:
            return None
        try:
            from llm.service import chat
            from llm.web_client import extract_json_block

            fields = self.get_fields()
            prompt = (
                "请仔细查看这张户口本照片，提取以下字段，严格返回一个 JSON 对象"
                "（字段缺失时用空字符串，不要输出任何其他文字）：\n"
                f"{json.dumps(fields, ensure_ascii=False)}"
            )
            reply = chat(prompt, images=[image_path])
            data = extract_json_block(reply)
            if not isinstance(data, dict):
                return None
            return {f: str(data.get(f, "") or "") for f in fields}
        except Exception:  # noqa: BLE001 — 失败则交给调用方走两步链路
            return None

    # ------------------------------------------------------------------
    # 常规两步：PaddleOCR 原始文本 → LLM 提取
    # ------------------------------------------------------------------
    def _extract_two_step(self, image_path: str) -> Dict[str, Any]:
        paddle = PaddleOCR()
        raw_text = paddle.recognize_text(image_path)

        llm = LLMExtractor()
        return llm.extract(raw_text, self.get_fields())

    def is_input_ready(self) -> bool:
        return self._image_path is not None

    @property
    def last_result(self) -> Dict[str, Any]:
        return self._last_result
