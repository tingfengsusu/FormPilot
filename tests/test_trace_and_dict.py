"""行号溯源 + 列名字典回写。

覆盖：
  - 本地列名字典：追加别名写进 import_mapping.local.yaml，加载时与基础字典合并
  - 导入建记录时带上**表格真实行号**（表头是第 1 行 → 数据从第 2 行起）
  - 单条「载入数据」能按行号取数（不再固定第一条）

全部用临时目录，不碰仓库配置。
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import yaml

from _helpers import use_temp_key

from batch.db import BatchDB
from batch.importer import Importer
from batch.recipe import Recipe
from core.column_dict import append_aliases, load_mapping, local_path_for

_CSV = """被保人姓名,被保人身份证,监护人电话,学校,年级,班级,保险期限
张小明,410305201509011230,13900000000,柳泉小学,三年级,三年级二班,8.22-8.21
李小红,410305201609021231,13900000001,柳泉小学,二年级,二年级一班,8.22-8.21
"""


def _write_csv(td: str) -> str:
    path = os.path.join(td, "台账.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(_CSV)
    return path


# ----------------------------------------------------------------------
def test_local_dictionary_append_and_merge():
    with tempfile.TemporaryDirectory() as td:
        base = os.path.join(td, "import_mapping.yaml")
        with open(base, "w", encoding="utf-8") as f:
            yaml.safe_dump({"columns": {"phone": ["手机号"]},
                            "ignore_columns": ["序号"]}, f, allow_unicode=True)

        local = local_path_for(base)
        assert append_aliases(local, {"phone": ["联系电话2"]}) == 1
        assert append_aliases(local, {"phone": ["联系电话2"]}) == 0    # 重复不写

        merged = load_mapping(base)
        assert merged["columns"]["phone"] == ["手机号", "联系电话2"]
        assert merged["ignore_columns"] == ["序号"]                    # 基础保留
        # 基础文件没被改（注释/排版是人的，机器只写 .local）
        with open(base, encoding="utf-8") as f:
            base_data = yaml.safe_load(f)
        assert base_data["columns"]["phone"] == ["手机号"]


def test_import_records_real_row_numbers():
    with tempfile.TemporaryDirectory() as td:
        use_temp_key(td)
        path = _write_csv(td)
        db = BatchDB(os.path.join(td, "t.db"))
        try:
            report = Importer(db).import_file(path, batch_name="行号测试")
            applicants = db.list_applicants(report.batch.id)
            assert [a.source_row for a in applicants] == [2, 3]     # 表头占第 1 行
            assert all(a.name for a in applicants)
        finally:
            db.close()


def test_import_records_product_and_preflight_blocks_mismatch():
    """批次记住是为哪个品种导入的；表头对不上当前品种时预检要拦住。"""
    import copy

    with tempfile.TemporaryDirectory() as td:
        use_temp_key(td)
        path = _write_csv(td)
        db = BatchDB(os.path.join(td, "t.db"))
        try:
            recipe = Recipe.load()
            report = Importer(db, recipe=recipe).import_file(path, batch_name="品种绑定")
            assert report.batch.product == recipe.product_name   # 记下品种

            # 换成发票查验品种：这份学平险台账覆盖不足（不是"品种选错"那么简单）
            other = copy.deepcopy(recipe)
            other.execute["product"] = "发票查验_国税总局"
            ok, detail = Importer(db, recipe=other).check_table(path)
            assert not ok and ("覆盖不足" in detail or "没能认出" in detail)

            # 用回学平险品种：能对上
            ok, detail = Importer(db, recipe=recipe).check_table(path)
            assert ok and "覆盖" in detail
        finally:
            db.close()


def test_new_business_table_points_to_alignment():
    """新业务的表（发票列名不在学平险字典里）→ 提示"先去对齐字段"，而不是怪品种选错。"""
    import copy

    invoice_csv = """发票代码,发票号码,开票日期,开具金额(不含税)
011002100311,25312000000012345678,2026-10-20,1130.00
"""
    with tempfile.TemporaryDirectory() as td:
        use_temp_key(td)
        path = os.path.join(td, "发票.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(invoice_csv)

        db = BatchDB(os.path.join(td, "t.db"))
        try:
            recipe = copy.deepcopy(Recipe.load())
            recipe.execute["product"] = "发票查验_国税总局"
            ok, detail = Importer(db, recipe=recipe).check_table(path)

            assert not ok
            assert "认不出" in detail                # 是字典不认列名，不是品种错
            assert "从样例表对齐字段" in detail       # 给出可操作的下一步
        finally:
            db.close()


def test_load_one_row_by_index():
    """单条「载入数据」按行号取数（第 2 行 = 表里第 3 行）。"""
    from PyQt5.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    from gui.main_window import MainWindow

    with tempfile.TemporaryDirectory() as td:
        path = _write_csv(td)
        first = MainWindow._record_from_file(path, 0)
        second = MainWindow._record_from_file(path, 1)

        assert first["学生姓名"] == "张小明"
        assert second["学生姓名"] == "李小红"
        assert second["班级"] == "二年级一班"
    app.processEvents()


if __name__ == "__main__":
    test_local_dictionary_append_and_merge()
    print("  · 本地列名字典：追加/合并/不动基础文件 ✓")
    test_import_records_real_row_numbers()
    print("  · 导入记录带真实行号（表头第 1 行 → 数据从第 2 行）✓")
    test_import_records_product_and_preflight_blocks_mismatch()
    print("  · 批次记住品种 + 预检拦住「覆盖不足」（不是简单对不上）✓")
    test_new_business_table_points_to_alignment()
    print("  · 新业务表 → 提示先「从样例表对齐字段」（不误杀正确台账）✓")
    test_load_one_row_by_index()
    print("  · 载入数据：可按行号取（不再固定第一条）✓")
    print("test_trace_and_dict.py 全部通过 ✓")
