"""真实数据体检（records_final.xlsx，119 条）—— 只在数据文件存在时运行。

用途：拿实测数据验证校验层（设计文档附录 A / B.1：这份 Excel 是 OCR 产物，
校验层是链路里唯一的自动化质量闸门）。

断言的都是**与数据内容无关的结构性结论**：
  - 119 条全部读入（列名映射能识别实测表头）
  - 被保人身份证校验位 119/119 通过；缴款人身份证非空的全部通过
  - 未探针产出页面选项时，19 种学校写法 / 18 种年级写法**全部未命中**
    （宁可见到未命中，也不要别名目标值绕过选项集合的假「导入成功」）

数据文件路径可用环境变量 FORMPILOT_REAL_XLSX 覆盖；
文件不存在（换机器/未同步）时跳过，不硬编码任何真实身份证号。
"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import skip_test, use_temp_key

from batch import id_card as ic
from batch.db import BatchDB
from batch.importer import Importer
from batch.recipe import Recipe

DEFAULT_XLSX = r"C:\Users\19845\WorkBuddy\2026-08-27-23-10-13\records_final.xlsx"


def _data_path() -> str:
    return os.environ.get("FORMPILOT_REAL_XLSX") or DEFAULT_XLSX


def test_real_data_quality():
    path = _data_path()
    if not os.path.exists(path):
        skip_test(f"未找到真实数据文件：{path}")
        return

    with tempfile.TemporaryDirectory() as td:
        use_temp_key(td)
        importer = Importer(BatchDB(os.path.join(td, "real.db")),
                            recipe=Recipe.load())
        try:
            report = importer.import_file(path, batch_name="真实数据体检")
            db_path = os.path.join(td, "real.db")
        finally:
            importer.db.close()

        # —— 读取与映射 ——
        assert report.total_rows == 119, report.summary
        assert report.imported == 119, report.summary
        assert not report.row_errors, report.row_errors

        with contextlib.closing(BatchDB(db_path)) as db:
            applicants = db.list_applicants(report.batch.id)

        # —— 身份证校验位（实测：被保人 119/119，缴款人 118/119 空 1 条）——
        student_ids = [a.id_card for a in applicants]
        assert len(student_ids) == 119
        assert all(ic.check_checksum(s) for s in student_ids), \
            [s[:6] for s in student_ids if not ic.check_checksum(s)]
        payer_ids = [a.payer_id_card for a in applicants if a.payer_id_card]
        assert all(ic.check_checksum(s) for s in payer_ids)

        # —— 日期规则：只有缺区间/非法日期的才留空 ——
        empty_period = [a for a in applicants if not a.period_start]
        assert 0 <= len(empty_period) <= 10, len(empty_period)

        # —— 起期约束：页面只允许「今天及之后」，台账里的 8 月分期会被平移 ——
        from batch.validator import resolve_period_floor
        floor = resolve_period_floor("today")
        assert floor is not None
        assert report.period_adjusted >= 100, report.period_adjusted
        abnormal_days = []
        for applicant in applicants:
            if applicant.period_start:
                from datetime import date as _date
                assert _date.fromisoformat(applicant.period_start) >= floor, \
                    (applicant.id, applicant.period_start)
                days = (_date.fromisoformat(applicant.period_end)
                        - _date.fromisoformat(applicant.period_start)).days
                # 平移保持原时长：原本就不在 360~370 的（实测 8.27-8.21 = 359 天）
                # 平移后仍然异常——这是对的，交给校验层判数据异常
                if not 360 <= days <= 370:
                    abnormal_days.append(days)
        assert len(abnormal_days) <= 3, abnormal_days

        # —— 字典：已探针产出的选项 + 已人工确认的别名，未命中的只应是「待确认」的写法 ——
        assert not report.unmatched.get("class_name"), report.unmatched.get("class_name")
        # 学校：19 种写法里 3 种已确认（柳小/柳中/柳幼）→ 未命中不超过 16 种
        assert len(report.unmatched.get("school", [])) <= 16, \
            report.unmatched.get("school")
        # 年级：18 种写法里 9 种已确认（一~六、高一~高三）→ 未命中不超过 9 种
        assert len(report.unmatched.get("grade", [])) <= 9, \
            report.unmatched.get("grade")
        # 字典未命中时状态必须是「数据异常」，不能混进「未确认」
        assert report.passed + report.data_error == 119

        print(f"  真实数据：{report.total_rows} 条读入，"
              f"校验通过 {report.passed}，数据异常 {report.data_error}")
        if report.period_adjusted:
            print(f"  起期平移 {report.period_adjusted} 条"
                  f"（页面只允许今天及之后）— 例："
                  f"{report.period_adjust_samples[0] if report.period_adjust_samples else '-'}")
        for key, values in sorted(report.unmatched.items()):
            print(f"  未命中（{key}）{len(values)} 种：{'、'.join(values)}")


if __name__ == "__main__":
    test_real_data_quality()
    print("test_batch_real_data.py 全部通过 ✓")
