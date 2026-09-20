"""执行层：一条消息任务 → 表单已填写 + 截图（复用 FormPilot 现有能力）。

设计文档 4.3 的定位是「只做一层薄封装」：OCR / 填表 / 回读都调现有代码，
这样 FormPilot 单机可用的能力，消息驱动也直接具备。
"""
from .run import FormPilotExecutor, VarietyConfig, load_varieties

__all__ = ["FormPilotExecutor", "VarietyConfig", "load_varieties"]
