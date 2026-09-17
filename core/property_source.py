"""属性来源抽象接口。

所有数据源（身份证 OCR、户口本 OCR、Excel、全局用户信息等）都必须
实现 :class:`PropertySource` 接口，从而做到「可插拔」：新增来源无需改动
核心代码。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class PropertySource(ABC):
    """属性来源抽象接口 — 所有数据源必须实现。

    一个「属性来源」代表一种能向外提供若干字段值的数据输入，例如：
      - 身份证 OCR：从证件图片提取 姓名/性别/民族/身份证号 等
      - 全局用户信息：手动配置的保险员信息
      - Excel：从名单文件读取学生信息
    """

    #: 来源显示名，如 "身份证OCR"。子类必须覆盖。
    name: str = ""

    #: 是否接受文件/图片输入（False 表示手动配置型来源，如保险员信息）。
    accepts_file_input: bool = True

    @abstractmethod
    def get_fields(self) -> list[str]:
        """返回该来源能提供的属性名列表。"""
        raise NotImplementedError

    @abstractmethod
    def extract(self, input_data: Any) -> Dict[str, Any]:
        """从原始输入（图片路径 / 文件路径 / 手输数据）解析出属性字典。"""
        raise NotImplementedError

    @abstractmethod
    def is_input_ready(self) -> bool:
        """是否已有输入（图片已上传 / 文件已选）。"""
        raise NotImplementedError