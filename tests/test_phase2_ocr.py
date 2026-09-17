"""Phase 2 OCR 识别 + 字段映射测试。

验证拖图 → extract → 字段名映射（源字段名 → 品种属性名）整条链路。

注意：Mock 相关用例显式固定 mock_mode，与 settings.yaml 当前状态
（可能已接入真实 OCR / LLM 通道）解耦。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as config_module
from sources.factory import create_source
from sources.idcard_ocr import IdCardOCR
from sources.household_ocr import HouseholdOCR
from sources.excel_source import ExcelSource


def _force_mock_mode():
    """这些用例验证 Mock 行为，显式固定 mock_mode（不受真实通道影响）。"""
    config_module.config._data = {"mock_mode": True}


def test_factory_create_source():
    assert isinstance(create_source("身份证OCR"), IdCardOCR)
    assert isinstance(create_source("户口本OCR"), HouseholdOCR)
    assert isinstance(create_source("Excel名单"), ExcelSource)
    assert create_source("不存在的来源") is None


def test_idcard_extract_mock_fields():
    _force_mock_mode()
    source = create_source("身份证OCR")
    result = source.extract("fake.png")
    # Mock 模式返回测试数据，且带 _source 标记
    assert result["姓名"] == "测试学生"
    assert result["身份证号"] == "410300201509011234"
    assert result.get("_source") == "mock"


def test_household_extract_mock_fields():
    _force_mock_mode()
    source = create_source("户口本OCR")
    result = source.extract("fake.png")
    assert result["户主姓名"] == "王大力"
    assert result.get("_source") == "mock"


def test_household_vision_path_without_paddle():
    """无本地 PaddleOCR 时：走网页版多模态直发图片的提取路径。"""
    _force_mock_mode()
    config_module.config._data = {"mock_mode": False, "llm": {"mode": "web"}}

    from ocr import paddle_ocr
    from sources import household_ocr
    from llm import service

    old_installed = paddle_ocr.PaddleOCR.is_installed
    old_chat = service.chat
    paddle_ocr.PaddleOCR.is_installed = staticmethod(lambda: False)
    service.chat = lambda prompt, images=None, timeout=None: (
        '{"户主姓名": "王大力", "与户主关系": "户主", "户号": "410300000000", '
        '"成员1姓名": "王小力", "成员1身份证号": "410305201509011235"}')
    try:
        result = household_ocr.HouseholdOCR().extract("fake.png")
        assert result["户主姓名"] == "王大力"
        assert result["成员1身份证号"] == "410305201509011235"
        assert "_fallback_reason" not in result
    finally:
        paddle_ocr.PaddleOCR.is_installed = old_installed
        service.chat = old_chat


def test_household_fallback_marks_reason():
    """多模态失败 + 无本地 OCR：回退并标注原因（不中断链路）。"""
    config_module.config._data = {"mock_mode": False, "llm": {"mode": "web"}}

    from ocr import paddle_ocr
    from sources import household_ocr
    from llm import service

    old_installed = paddle_ocr.PaddleOCR.is_installed
    old_chat = service.chat
    paddle_ocr.PaddleOCR.is_installed = staticmethod(lambda: False)
    service.chat = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("网页版不可用"))
    try:
        result = household_ocr.HouseholdOCR().extract("fake.png")
        assert result.get("_source") == "mock"          # 最终回退 Mock
        assert "_fallback_reason" in result             # 并说明原因
    finally:
        paddle_ocr.PaddleOCR.is_installed = old_installed
        service.chat = old_chat


def test_field_name_mapping():
    """源字段「姓名」应能映射到品种属性「学生姓名」。"""
    # 复用 SourceGroup 的映射逻辑思路（此处直接验证包含规则）
    prop = "学生姓名"
    src_values = {"姓名": "测试学生", "身份证号": "410300201509011234"}
    matched = None
    for src_field, v in src_values.items():
        if src_field and src_field in prop:
            matched = v
            break
    assert matched == "测试学生"

    # "学生身份证号" 应匹配 "身份证号"
    prop2 = "学生身份证号"
    matched2 = None
    for src_field, v in src_values.items():
        if src_field and src_field in prop2:
            matched2 = v
            break
    assert matched2 == "410300201509011234"


if __name__ == "__main__":
    test_factory_create_source()
    test_idcard_extract_mock_fields()
    test_household_extract_mock_fields()
    test_household_vision_path_without_paddle()
    test_household_fallback_marks_reason()
    test_field_name_mapping()
    print("test_phase2_ocr.py 全部通过 ✓")