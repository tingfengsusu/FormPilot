"""Excel 文件读取来源。

Phase 2：接入真实 Excel 读取（openpyxl/pandas 惰性导入），
未安装或 Mock 模式时回退 Mock。
"""
from __future__ import annotations

from typing import Any, Dict

from core.property_source import PropertySource
from core.config import config
from core import mock_data


class ExcelSource(PropertySource):
    """Excel 名单读取属性来源。"""

    name = "Excel名单"

    def __init__(self) -> None:
        self._file_path: str | None = None
        self._last_result: Dict[str, Any] = {}

    def get_fields(self) -> list[str]:
        return ["学校", "班级", "学生姓名", "监护人姓名", "监护人电话"]

    def extract(self, input_data: Any) -> Dict[str, Any]:
        file_path = input_data if isinstance(input_data, str) else str(input_data)
        self._file_path = file_path

        if config.mock_mode:
            result = mock_data.excel_mock(file_path)
        else:
            try:
                result = self._read_excel(file_path)
            except Exception:
                result = mock_data.excel_mock(file_path)

        self._last_result = result
        return result

    def _read_excel(self, file_path: str) -> Dict[str, Any]:
        """真实读取 Excel 第一行作为字段值（openpyxl 惰性导入）。"""
        import openpyxl

        wb = openpyxl.load_workbook(file_path, data_only=True)
        ws = wb.active

        # 约定：第一行表头，第二行数据（骨架阶段只读第一条记录）
        headers = [c.value for c in ws[1]]
        values = [c.value for c in ws[2]] if ws.max_row >= 2 else []
        result: Dict[str, Any] = {}
        for i, h in enumerate(headers):
            if h is None:
                continue
            result[str(h)] = values[i] if i < len(values) else ""
        return result

    def is_input_ready(self) -> bool:
        return self._file_path is not None

    @property
    def last_result(self) -> Dict[str, Any]:
        return self._last_result