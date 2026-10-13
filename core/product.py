"""品种数据类。

一个「品种」对应一种录入场景（如「太保洛阳学平险_基础」），配置了：
  - 目标页面 URL
  - 需要填写哪些属性（属性名 → 页面输入框的映射）
  - 提交按钮位置、回读校验延迟等
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class FieldMapping:
    """单个字段映射：品种属性名 ↔ 页面输入框。"""

    property: str                      # 属性名，如 "学生姓名"
    locator: str                       # CSS 选择器，如 'input[name="xm"]'
    required: bool = True              # 是否必填
    verify_after_fill: bool = True     # 填完后是否回读校验
    rule: Optional[str] = None         # 可选规则引擎标识，如 "recommend_by_grade"
    # 填写策略：
    #   plain        直接填（自动区分 input / select）
    #   autocomplete 联想选择框（12306 站点这类写值触发联想的）
    #   click        点击型选择（方案卡片/标签页）
    #   select       下拉选择（学校/年级/班级；兼容原生 select 与自定义组件）
    #   date         日期选择器（保险期限；原生输入框或滚轮/日历面板）
    #   verify_only  **只回读校验、不填写**（页面自动算出来的字段，
    #                如实测的「保险期间止」= 起期 + 364 天，且点不开）
    fill_mode: str = "plain"
    # 复核呈现档位（"人工呈现"这一档；机器回读由 verify_after_fill 单独控制）：
    #   human    进人工复核呈现（批注层贴期望值 / 报告里列出来）—— 默认
    #   machine  不进人工呈现（纯直填的常规字段，少些噪音）；
    #            一旦回读出问题，批注与报告仍会把它挑出来
    #   spot     降级为抽查（呈现沿用 human，抽查统计留给后续实现）
    verify_tier: str = "human"

    @classmethod
    def from_dict(cls, data: dict) -> "FieldMapping":
        return cls(
            property=data["property"],
            locator=data["locator"],
            required=data.get("required", True),
            verify_after_fill=data.get("verify_after_fill", True),
            rule=data.get("rule"),
            fill_mode=data.get("fill_mode", "plain") or "plain",
            verify_tier=data.get("verify_tier", "human") or "human",
        )


@dataclass
class Product:
    """品种数据类 — 某个录入场景的完整配置。"""

    name: str
    description: str = ""
    target_url: str = ""
    field_mapping: List[FieldMapping] = field(default_factory=list)
    submit_button: str = 'button[type="submit"]'
    verify_delay_ms: int = 500
    # 该品种需要哪些属性来源（由 field_mapping 推导，也可显式声明）
    required_sources: List[str] = field(default_factory=list)
    # 浏览器上下文配置（移动端/微信 H5 等）：
    #   {"mobile": true, "wechat": true, "referer": "...", "user_agent": "..."}
    context: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "Product":
        mapping = [FieldMapping.from_dict(m) for m in data.get("field_mapping", [])]
        return cls(
            name=data.get("name", ""),
            description=data.get("description", ""),
            target_url=data.get("target_url", ""),
            field_mapping=mapping,
            submit_button=data.get("submit_button", 'button[type="submit"]'),
            verify_delay_ms=int(data.get("verify_delay_ms", 500)),
            required_sources=list(data.get("required_sources", [])),
            context=dict(data.get("context") or {}),
        )

    def property_names(self) -> list[str]:
        """返回该品种需要填写的所有属性名。"""
        return [m.property for m in self.field_mapping]

    def to_dict(self) -> dict:
        """序列化为 yaml 可写格式（用于品种创建器保存）。"""
        return {
            "name": self.name,
            "description": self.description,
            "target_url": self.target_url,
            "field_mapping": [
                {
                    "property": m.property,
                    "locator": m.locator,
                    "required": m.required,
                    "verify_after_fill": m.verify_after_fill,
                    **({"rule": m.rule} if m.rule else {}),
                    **({"fill_mode": m.fill_mode} if m.fill_mode != "plain" else {}),
                    **({"verify_tier": m.verify_tier}
                       if m.verify_tier != "human" else {}),
                }
                for m in self.field_mapping
            ],
            "submit_button": self.submit_button,
            "verify_delay_ms": self.verify_delay_ms,
            "required_sources": list(self.required_sources),
            **({"context": dict(self.context)} if self.context else {}),
        }