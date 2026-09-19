"""batch/importer.py 测试 —— 列名映射 / 占位符 / 日期补年份 / 导入落库。

覆盖：
  - ColumnMapper：多别名列名解析（「缴款人姓名」不被「姓名」抢走）、忽略列、占位符当空
  - 日期规则：保险期限「8.22-8.21」→ 止日期按次年解释；缺区间/非法日期 → 异常
  - 导入落库：状态机初始状态、validation 写库、批次计数、身份证加密存储
  - 字典未命中 → 数据异常 + 未命中清单（不猜）
  - 批次内重复投保（同身份证 + 同期间）→ 两条都进数据异常

测试样本全部虚构；身份证号由 _helpers.make_id_card 现场生成。
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _helpers import make_id_card, use_temp_key

from batch.db import BatchDB
from batch.dictionaries import Dictionary, DictionarySet
from batch.importer import ColumnMapper, Importer, read_table
from batch.models import STATUS_DATA_ERROR, STATUS_PENDING
from batch.recipe import Recipe

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")

HEADERS = ["序号", "投保人姓名", "缴款人姓名", "缴款人身份证", "缴款人手机号",
           "被保人姓名", "被保人身份证", "被保人手机号",
           "学校（保留原名）", "年级", "班级", "保险期限"]

STUDENT_ID = make_id_card("410305", "20150901", "12")   # 2015-09-01 生
STUDENT_ID_2 = make_id_card("410305", "20160402", "56")  # 2016-04-02 生
PAYER_ID = make_id_card("410305", "19850312", "34", "女")


def _dicts(options_ready: bool = True) -> DictionarySet:
    if not options_ready:
        return DictionarySet({
            "school": Dictionary("school", aliases={"柳小": "柳集小学"}),
            "grade": Dictionary("grade"),
            "class_name": Dictionary("class_name"),
        })
    return DictionarySet({
        "school": Dictionary("school", options=["柳集小学", "实验小学"],
                             aliases={"柳小": "柳集小学"}),
        "grade": Dictionary("grade", options=["一年级"], aliases={"一": "一年级"}),
        "class_name": Dictionary("class_name", options=["1班"]),
    })


def _row(**kwargs) -> dict:
    row = {"序号": 1, "投保人姓名": "（投保人姓名）", "缴款人姓名": "家长甲",
           "缴款人身份证": PAYER_ID, "缴款人手机号": "13900000000",
           "被保人姓名": "学生甲", "被保人身份证": STUDENT_ID,
           "被保人手机号": "13800000000", "学校（保留原名）": "柳小",
           "年级": "一", "班级": "1班", "保险期限": "8.22-8.21"}
    row.update(kwargs)
    return row


@contextlib.contextmanager
def _importer(db_path: str, options_ready: bool = True,
              period_start_floor: str = "off"):
    """建库 + 建导入器，退出时关闭连接（Windows 下临时目录才能删除）。

    ``period_start_floor`` 默认关掉：本文件大部分用例在测日期解析规则本身；
    起期平移策略由 test_period_floor_shifts_start 单独覆盖。
    """
    use_temp_key(os.path.dirname(db_path))
    recipe = Recipe.load()
    recipe.input_config["period_start_floor"] = period_start_floor
    importer = Importer(BatchDB(db_path), recipe=recipe,
                        dictionaries=_dicts(options_ready))
    try:
        yield importer
    finally:
        importer.db.close()


# ----------------------------------------------------------------------
# 列名映射
# ----------------------------------------------------------------------
def test_column_mapper_resolution():
    mapper = ColumnMapper({
        "columns": {
            "name": ["被保人姓名", "学生姓名", "姓名"],
            "payer_name": ["缴款人姓名", "家长姓名"],
            "id_card": ["被保人身份证", "身份证号"],
            "phone": ["被保人手机号", "手机号"],
        },
        "ignore_columns": ["序号"],
    })
    resolved = mapper.resolve(HEADERS)
    # 「缴款人姓名」必须归 payer_name，不能被短别名「姓名」抢走
    assert resolved["name"] == "被保人姓名"
    assert resolved["payer_name"] == "缴款人姓名"
    assert resolved["id_card"] == "被保人身份证"
    assert resolved["phone"] == "被保人手机号"


def _load_real_mapping() -> dict:
    import yaml
    with open(os.path.join(CONFIG_DIR, "import_mapping.yaml"),
              "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_column_mapper_real_config():
    """真实配置（config/import_mapping.yaml）能解析实测表头。"""
    mapper = ColumnMapper(_load_real_mapping())
    resolved = mapper.resolve(HEADERS)
    expected = {
        "name": "被保人姓名", "id_card": "被保人身份证", "phone": "被保人手机号",
        "payer_name": "缴款人姓名", "payer_id_card": "缴款人身份证",
        "payer_phone": "缴款人手机号", "applicant_name": "投保人姓名",
        "school": "学校（保留原名）", "grade": "年级", "class_name": "班级",
        "period": "保险期限",
    }
    for key, column in expected.items():
        assert resolved.get(key) == column, f"{key} → {resolved.get(key)}"


def test_clean_placeholders_and_numbers():
    mapper = ColumnMapper(_load_real_mapping())
    assert mapper.clean("（投保人姓名）") == ""
    assert mapper.clean("投保人姓名") == ""
    assert mapper.clean("  家长甲  ") == "家长甲"
    assert mapper.clean(13800000000.0) == "13800000000"
    assert mapper.clean(None) == ""


# ----------------------------------------------------------------------
# 日期规则
# ----------------------------------------------------------------------
def test_period_year_rule():
    with tempfile.TemporaryDirectory() as td:
        with _importer(os.path.join(td, "t.db")) as importer:
            columns = importer.mapper.resolve(HEADERS)

            applicant = importer._build_applicant(_row(), columns)
            assert applicant.period_start == "2026-08-22"
            assert applicant.period_end == "2027-08-21"      # 止日期 = 次年

            full = importer._build_applicant(
                _row(保险期限="2026-09-01 至 2027-08-31"), columns)
            assert full.period_start == "2026-09-01"
            assert full.period_end == "2027-08-31"

            # 缺区间 / 非法日期 → 期间留空，由校验层判异常
            for bad in ("9.12", "9.1-9.0"):
                assert importer._build_applicant(
                    _row(保险期限=bad), columns).period_start == "", bad

            # 时长异常（359 天）：日期能解析，但校验层必须判异常
            short = importer._build_applicant(_row(保险期限="8.27-8.21"), columns)
            assert short.period_start == "2026-08-27"
            assert short.period_end == "2027-08-21"


def test_period_floor_shifts_start():
    """页面只允许「今天及之后」的起期：早于起期下限时整体平移 + 留痕提醒。"""
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batch.db")
        with _importer(db_path, period_start_floor="2026-09-19") as importer:
            columns = importer.mapper.resolve(HEADERS)

            shifted = importer._build_applicant(_row(), columns)
            assert shifted.period_start == "2026-09-19"        # 平移到下限
            assert shifted.period_end == "2027-09-18"          # 保持 364 天
            assert shifted.adjust_notes and "起期" in shifted.adjust_notes[0]
            assert "2026-08-22" in shifted.adjust_notes[0]     # 留痕带上原值

            # 起期已在下限之后 → 不动
            later = importer._build_applicant(
                _row(保险期限="2026-10-01 至 2027-09-30"), columns)
            assert later.period_start == "2026-10-01"
            assert later.adjust_notes == []

            report = importer.import_rows(
                [_row(), _row(被保人姓名="学生乙", 被保人身份证=STUDENT_ID_2)],
                HEADERS)
            assert report.period_adjusted == 2
            assert len(report.period_adjust_samples) == 2
            assert "起期" in report.summary and "请人工确认" in report.summary

        # 调整留痕进 validation 表（passed=True，不影响通过与否）
        with contextlib.closing(BatchDB(db_path)) as db:
            applicants = db.list_applicants(report.batch.id)
            first = db.get_applicant(applicants[0].id)
            notes = [v for v in first.validations if v.rule == "period_adjusted"]
            assert notes and "台账原起期" in notes[0].detail
            assert first.period_start.startswith("2026-09-19")


# ----------------------------------------------------------------------
# 端到端导入
# ----------------------------------------------------------------------
def test_import_end_to_end():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batch.db")
        with _importer(db_path) as importer:
            rows = [_row(), _row(被保人姓名="学生乙", 被保人身份证=STUDENT_ID_2)]
            report = importer.import_rows(rows, HEADERS, batch_name="测试批次")

            assert report.total_rows == 2 and report.imported == 2
            assert report.passed == 2 and report.data_error == 0
            assert report.options_ready is True
            assert report.batch.total == 2

        with contextlib.closing(BatchDB(db_path)) as db:
            applicants = db.list_applicants(report.batch.id, with_validations=True)
            assert [a.status for a in applicants] == [STATUS_PENDING, STATUS_PENDING]
            assert applicants[0].school == "柳集小学"        # 别名归一化
            assert applicants[0].grade == "一年级"
            assert applicants[0].student_level == "小学生"    # 由原始年级写法推导
            assert applicants[0].applicant_name == "李四"     # 占位符 → 当前操作者
            assert applicants[0].id_card == STUDENT_ID        # 读回解密
            assert any(r.rule == "checksum" and r.passed
                       for r in applicants[0].validations)

        # 身份证号在库里是密文（设计文档第七节：加密存储）
        with contextlib.closing(sqlite3.connect(db_path)) as conn:
            raw = conn.execute(
                "SELECT id_card FROM applicant ORDER BY id LIMIT 1").fetchone()[0]
        assert raw.startswith("enc:") and STUDENT_ID not in raw


def test_import_unmatched_to_data_error():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batch.db")
        with _importer(db_path) as importer:
            rows = [_row(), _row(被保人姓名="学生乙", 被保人身份证=STUDENT_ID_2,
                                 **{"学校（保留原名）": "清花园"})]
            report = importer.import_rows(rows, HEADERS)

        assert report.passed == 1 and report.data_error == 1
        assert report.unmatched.get("school") == ["清花园"]
        assert any("清花园" in reason for _, reason in report.errors)
        assert "字典未命中（school）" in report.summary

        with contextlib.closing(BatchDB(db_path)) as db:
            statuses = {a.name: a.status for a in db.list_applicants(report.batch.id)}
        assert statuses["学生乙"] == STATUS_DATA_ERROR
        assert statuses["学生甲"] == STATUS_PENDING


def test_import_duplicate_flags_both():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "batch.db")
        with _importer(db_path) as importer:
            rows = [_row(), _row(被保人姓名="学生乙")]     # 同身份证 + 同期间
            report = importer.import_rows(rows, HEADERS)

        assert report.data_error == 2
        assert all("重复投保" in reason for _, reason in report.errors)

        with contextlib.closing(BatchDB(db_path)) as db:
            for applicant in db.list_applicants(report.batch.id):
                assert applicant.status == STATUS_DATA_ERROR


def test_import_skips_empty_rows():
    with tempfile.TemporaryDirectory() as td:
        with _importer(os.path.join(td, "batch.db")) as importer:
            empty = {key: "" for key in HEADERS}
            report = importer.import_rows([_row(), empty], HEADERS)
            assert report.total_rows == 2 and report.imported == 1
            assert report.skipped == 1


def test_read_table_csv_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "名单.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("被保人姓名,被保人身份证,学校（保留原名）\n学生甲,"
                    + STUDENT_ID + ",柳小\n")
        headers, rows = read_table(path)
        assert headers[0] == "被保人姓名" and len(rows) == 1
        assert rows[0]["学校（保留原名）"] == "柳小"


# ----------------------------------------------------------------------
# 回归：过渡模式已废弃（选项未探针产出时不得假「导入成功」）
# ----------------------------------------------------------------------
def test_options_not_ready_must_not_fake_success():
    """options_*.yaml 为空时，别名目标值不得绕过选项集合检查。

    宁可看到「学校写法全部未命中」，也不要一个假的「导入成功」。
    """
    with tempfile.TemporaryDirectory() as td:
        with _importer(os.path.join(td, "batch.db"), options_ready=False) as importer:
            report = importer.import_rows([_row()], HEADERS)

        assert report.options_ready is False
        assert report.passed == 0 and report.data_error == 1
        assert report.unmatched.get("school") == ["柳小"]
        assert "探针" in report.summary


if __name__ == "__main__":
    test_column_mapper_resolution()
    test_column_mapper_real_config()
    test_clean_placeholders_and_numbers()
    test_period_year_rule()
    test_period_floor_shifts_start()
    test_import_end_to_end()
    test_import_unmatched_to_data_error()
    test_import_duplicate_flags_both()
    test_import_skips_empty_rows()
    test_read_table_csv_roundtrip()
    test_options_not_ready_must_not_fake_success()
    print("test_batch_importer.py 全部通过 ✓")
