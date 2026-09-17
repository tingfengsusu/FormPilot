"""属性来源接口测试。"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.property_pool import PropertyPool
from sources.global_user_info import GlobalUserInfo
from sources.idcard_ocr import IdCardOCR
from sources.household_ocr import HouseholdOCR
from sources.excel_source import ExcelSource


def _fresh_global_user_info() -> GlobalUserInfo:
    """用临时路径构造，避免读到真实的 config/global_user_info.yaml。"""
    return GlobalUserInfo(path=os.path.join(tempfile.mkdtemp(), "g.yaml"))


def test_property_source_interface():
    # 各来源都实现 get_fields / extract / is_input_ready
    for source in [_fresh_global_user_info(), IdCardOCR(), HouseholdOCR(), ExcelSource()]:
        fields = source.get_fields()
        assert isinstance(fields, list) and fields
        assert not source.is_input_ready()


def test_global_user_info_persistence():
    """保险员信息：保存后新实例自动加载（配置一次反复用）。"""
    path = os.path.join(tempfile.mkdtemp(), "g.yaml")
    source = GlobalUserInfo(path=path)
    assert not source.is_input_ready()

    source.extract({"保险员姓名": "张三", "保险员电话": "13800000000"})
    source.save()

    reloaded = GlobalUserInfo(path=path)
    assert reloaded.is_input_ready()
    assert reloaded.extract(None)["保险员姓名"] == "张三"


def test_global_user_info_manual_only():
    """手动配置型来源：不接收文件输入（UI 据此隐藏上传入口）。"""
    assert GlobalUserInfo.accepts_file_input is False
    assert IdCardOCR.accepts_file_input is True


def test_idcard_ocr_mock():
    source = IdCardOCR()
    result = source.extract("dummy.png")
    assert result["姓名"] == "测试学生"
    assert source.is_input_ready()


def test_property_pool_feed_and_get():
    pool = PropertyPool()
    pool.register_source(IdCardOCR())
    pool.feed("身份证OCR", "dummy.png")
    assert pool.get("姓名") == "测试学生"
    assert "身份证号" in pool.all()


if __name__ == "__main__":
    test_property_source_interface()
    test_global_user_info_persistence()
    test_global_user_info_manual_only()
    test_idcard_ocr_mock()
    test_property_pool_feed_and_get()
    print("test_property_source.py 全部通过 ✓")