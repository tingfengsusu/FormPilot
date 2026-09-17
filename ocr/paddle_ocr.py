"""PaddleOCR 封装。

识别图片原始文本（带位置），用于户口本等非标证件的兜底。
未安装 PaddleOCR / 处于 Mock 模式时回退到模拟结果。
"""
from __future__ import annotations

from typing import Any, List

from core.config import config


class PaddleOCR:
    """PaddleOCR 封装（惰性加载）。"""

    def __init__(self, use_gpu: bool = False, lang: str = "ch"):
        self.use_gpu = use_gpu
        self.lang = lang
        self._engine = None  # 惰性初始化

    def _ensure_engine(self):
        if self._engine is None:
            from paddleocr import PaddleOCR as _PaddleOCR
            self._engine = _PaddleOCR(use_angle_cls=True, lang=self.lang,
                                      use_gpu=self.use_gpu)
        return self._engine

    @staticmethod
    def is_installed() -> bool:
        """本地是否安装了 PaddleOCR（未安装时 recognize 返回模拟文本）。"""
        try:
            import paddleocr  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def recognize(self, image_path: str) -> List[Any]:
        """识别图片原始文本（带位置）。返回 [(box, (text, confidence)), ...]。

        Mock 模式 / 未安装 PaddleOCR 时返回模拟结果。
        """
        if config.mock_mode:
            return self._mock_result(image_path)

        try:
            engine = self._ensure_engine()
            result = engine.ocr(image_path, cls=True)
            # 平铺成统一格式：[box, (text, conf)]
            lines: List[Any] = []
            for page in result or []:
                if isinstance(page, list):
                    lines.extend(page)
            return lines
        except Exception:
            return self._mock_result(image_path)

    def _mock_result(self, image_path: str) -> List[Any]:
        """模拟原始 OCR 文本。"""
        return [
            (None, ("王大力", 0.99)),
            (None, ("户主", 0.99)),
            (None, ("与户主关系", 0.98)),
            (None, ("王小力", 0.97)),
            (None, ("410300000000", 0.99)),
            (None, ("410300201509011234", 0.99)),
        ]

    def recognize_text(self, image_path: str) -> str:
        """只返回纯文本（拼接所有识别行），供 LLM 提取使用。"""
        lines = self.recognize(image_path)
        texts = []
        for _, (text, _conf) in lines:
            texts.append(str(text))
        return "\n".join(texts)