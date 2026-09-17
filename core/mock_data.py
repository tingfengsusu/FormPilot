"""Mock 数据提供器 — Phase 2 调试用。

当 settings.yaml 中 mock_mode=true，或对应 API key 未配置时，
OCR / LLM 引擎回退到这里的测试数据，保证「拖图 → 识别 → 回填」
链路能在没有真实凭证的情况下跑通。

所有 Mock 结果都带一个 `_source: "mock"` 标记，便于 GUI 展示时
明确告知用户「这是模拟结果」。
"""
from __future__ import annotations

from typing import Any, Dict, Optional


def _with_mock_mark(data: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(data)
    result["_source"] = "mock"
    return result


def idcard_mock(image_path: Optional[str] = None) -> Dict[str, Any]:
    """身份证 OCR Mock 结果。"""
    return _with_mock_mark({
        "姓名": "测试学生",
        "性别": "男",
        "民族": "汉",
        "出生日期": "2015-09-01",
        "身份证号": "410300201509011234",
        "地址": "河南省洛阳市测试路 1 号",
        "签发机关": "洛阳市公安局",
        "有效期": "2020.09.01-2030.09.01",
    })


def household_mock(image_path: Optional[str] = None) -> Dict[str, Any]:
    """户口本 OCR Mock 结果。"""
    return _with_mock_mark({
        "户主姓名": "王大力",
        "与户主关系": "父子",
        "户号": "410300000000",
        "成员1姓名": "王小力",
        "成员1身份证号": "410300201509011234",
    })


def excel_mock(file_path: Optional[str] = None) -> Dict[str, Any]:
    """Excel 名单 Mock 结果。"""
    return _with_mock_mark({
        "学校": "洛阳市实验小学",
        "班级": "三年级二班",
        "学生姓名": "测试学生",
        "监护人姓名": "王大力",
        "监护人电话": "13800000000",
    })