"""网页版 LLM 单元测试（不依赖浏览器与登录态）。

覆盖：
  - extract_json_block 的各种回复形态解析
  - LLMService 的模式路由与降级链（web → api → 报错）
  - 配置读取（llm.mode / llm.web.*）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config as config_module
from core.config import Config
from llm import service
from llm.web_client import extract_json_block


def test_extract_json_block():
    # 纯 JSON
    assert extract_json_block('{"a": 1}') == {"a": 1}
    # markdown 代码块
    assert extract_json_block('```json\n{"a": "中文"}\n```') == {"a": "中文"}
    # 无语言标记的代码块
    assert extract_json_block('```\n{"a": 2}\n```') == {"a": 2}
    # 前后带说明文字
    text = '好的，以下是映射结果：\n{"学生姓名": "input[name=\\"xm\\"]"}\n如需调整请告诉我。'
    assert extract_json_block(text) == {"学生姓名": 'input[name="xm"]'}
    # 失败：无 JSON
    try:
        extract_json_block("这里没有任何 JSON")
        raise AssertionError("无 JSON 时应抛 ValueError")
    except ValueError:
        pass


def test_extract_json_block_repairs_unescaped_quotes():
    """LLM 常见滑手：选择器里的双引号漏转义（实测线上回复出现过）。"""
    # 线上真实回复形态：值内部引号未转义
    raw = ('{"学生姓名":"input[name="xm"]","学生身份证号":"input[name="sfzh"]",'
           '"监护人电话":"input[name="lxdh"]","保险方案":"select[name="baoxian"]"}')
    parsed = extract_json_block(raw)
    assert parsed["学生姓名"] == 'input[name="xm"]'
    assert parsed["保险方案"] == 'select[name="baoxian"]'
    assert len(parsed) == 4

    # 与代码块/说明文字组合
    wrapped = f'好的，结果如下：\n```json\n{raw}\n```'
    assert extract_json_block(wrapped)["监护人电话"] == 'input[name="lxdh"]'

    # 规范 JSON（已正确转义）不受修复逻辑影响
    proper = '{"a":"input[name=\\"x\\"]","b":"普通值"}'
    assert extract_json_block(proper) == {"a": 'input[name="x"]', "b": "普通值"}


def test_config_llm_mode_and_web():
    cfg = Config(path="__not_exists__.yaml")
    cfg._data = {"llm": {"mode": "web", "web": {"headless": True,
                                                "reply_timeout_seconds": 60}}}
    assert cfg.llm_mode == "web"
    assert cfg.llm_web_config["headless"] is True
    assert cfg.has_llm() is True  # web 模式无需 key

    cfg._data = {"llm": {"mode": "api", "api_key": ""}}
    assert cfg.llm_mode == "api"
    assert cfg.has_llm() is False

    cfg._data = {"llm": {"mode": "api", "api_key": "sk-xxx"}}
    assert cfg.has_llm() is True

    # 默认（未配置 mode）为 api
    cfg._data = {"llm": {"api_key": "sk-yyy"}}
    assert cfg.llm_mode == "api"


def test_service_routes_web(monkeypatch_web_ok=True):
    """web 模式：走 web_chat。"""
    config_module.config._data = {"llm": {"mode": "web"}}
    calls = {"web": 0, "api": 0}

    def fake_web(prompt, images=None, timeout=None):
        calls["web"] += 1
        return "web-reply"

    old_web, old_api = service.web_chat, service.api_chat
    service.web_chat, service.api_chat = fake_web, lambda p: "api-reply"
    try:
        assert service.chat("hi") == "web-reply"
        assert calls == {"web": 1, "api": 0}
    finally:
        service.web_chat, service.api_chat = old_web, old_api


def test_service_web_falls_back_to_api():
    """web 失败 + 有 api_key → 回退 API。"""
    config_module.config._data = {"llm": {"mode": "web", "api_key": "sk-test"}}

    def failing_web(prompt, images=None, timeout=None):
        raise RuntimeError("浏览器崩了")

    old_web, old_api = service.web_chat, service.api_chat
    service.web_chat, service.api_chat = failing_web, lambda p: "api-reply"
    try:
        assert service.chat("hi") == "api-reply"
    finally:
        service.web_chat, service.api_chat = old_web, old_api


def test_service_web_failure_without_api_key_raises():
    """web 失败 + 无 api_key → 抛 LLMError（调用方再走启发式兜底）。"""
    config_module.config._data = {"llm": {"mode": "web", "api_key": ""}}

    def failing_web(prompt, images=None, timeout=None):
        raise RuntimeError("未登录")

    old_web = service.web_chat
    service.web_chat = failing_web
    try:
        try:
            service.chat("hi")
            raise AssertionError("应抛 LLMError")
        except service.LLMError as e:
            assert "网页版调用失败" in str(e)
    finally:
        service.web_chat = old_web


def test_matcher_falls_back_to_heuristic_on_llm_failure():
    """LLM 不可用时：FieldMatcher 回退启发式匹配（不抛异常）。"""
    config_module.config._data = {"llm": {"mode": "web"}, "mock_mode": False}
    from page.matcher import FieldMatcher

    matcher = FieldMatcher()
    old_call = matcher._call_llm
    matcher._call_llm = lambda prompt: (_ for _ in ()).throw(RuntimeError("LLM 不可用"))
    try:
        fields = [{"locator": 'input[name="xm"]', "placeholder": "请输入姓名",
                   "label": "学生姓名"}]
        mapping = matcher.match(fields, ["学生姓名"])
        assert mapping["学生姓名"] == 'input[name="xm"]'
    finally:
        matcher._call_llm = old_call


if __name__ == "__main__":
    test_extract_json_block()
    test_extract_json_block_repairs_unescaped_quotes()
    test_config_llm_mode_and_web()
    test_service_routes_web()
    test_service_web_falls_back_to_api()
    test_service_web_failure_without_api_key_raises()
    test_matcher_falls_back_to_heuristic_on_llm_failure()
    print("test_web_llm.py 全部通过 ✓")
