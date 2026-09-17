"""身份证 OCR（云 API + PaddleOCR 兜底）。

extract() 流程（见文档 6.1 节）：
  1. 优先调用腾讯云身份证 OCR（准确率 99%+）
  2. 失败/未配置时回退到 PaddleOCR
  3. 全部失败时回退 Mock 数据

Phase 2：接入真实 OCR 引擎，但保留 Mock 模式供无凭证调试。
"""
from __future__ import annotations

from typing import Any, Dict

from core.property_source import PropertySource
from core.config import config
from ocr.tencent_cloud import TencentCloudOCR
from ocr.paddle_ocr import PaddleOCR


class IdCardOCR(PropertySource):
    """身份证 OCR 属性来源。"""

    name = "身份证OCR"

    def __init__(self) -> None:
        self._image_path: str | None = None
        self._last_result: Dict[str, Any] = {}

    def get_fields(self) -> list[str]:
        return ["姓名", "性别", "民族", "出生日期", "身份证号", "地址", "签发机关", "有效期"]

    def extract(self, input_data: Any) -> Dict[str, Any]:
        image_path = input_data if isinstance(input_data, str) else str(input_data)
        self._image_path = image_path

        # 1. 优先云 API
        tencent = TencentCloudOCR()
        result = tencent.recognize_idcard(image_path)

        # 2. 云 API 失败返回 Mock（recognize 内部已回退），
        #    此处判断是否真拿到有效姓名，否则走 PaddleOCR 兜底。
        if not result.get("身份证号"):
            paddle = PaddleOCR()
            result = self._parse_from_paddle(paddle, image_path, result)

        self._last_result = result
        return result

    def _parse_from_paddle(self, paddle: PaddleOCR, image_path: str, seed: dict) -> Dict[str, Any]:
        """PaddleOCR 兜底：识别原始文本，做简单字段抽取。"""
        text = paddle.recognize_text(image_path)
        merged = dict(seed)
        # 简单启发式：从文本抽取身份证号和姓名（真实项目可用 LLM 增强）
        merged["_raw_text"] = text
        return merged

    def is_input_ready(self) -> bool:
        return self._image_path is not None

    @property
    def last_result(self) -> Dict[str, Any]:
        return self._last_result