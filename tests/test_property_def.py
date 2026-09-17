"""属性对象模型与属性库测试。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.property_def import (PropertyDef, PropertyDefLibrary,
                               builtin_defs, TYPE_SELECT, TYPE_PHONE, TYPE_DATE)


def test_property_def_roundtrip():
    d = PropertyDef(
        name="就读年级", source="Excel名单", field_type=TYPE_SELECT,
        options=["一年级", "二年级", "三年级"], default_value="一年级",
        required=True, description="学生所在年级",
    )
    d2 = PropertyDef.from_dict(d.to_dict())
    assert d2 == d


def test_builtin_defs_cover_all_sources():
    defs = builtin_defs()
    names = [d.name for d in defs]
    sources = {d.source for d in defs}

    # 四个内置来源都有属性
    assert sources == {"身份证OCR", "户口本OCR", "Excel名单", "保险员信息"}
    # 类型启发式推断
    by_name = {d.name: d for d in defs}
    assert by_name["性别"].field_type == TYPE_SELECT
    assert by_name["性别"].options == ["男", "女"]
    assert by_name["出生日期"].field_type == TYPE_DATE
    assert by_name["监护人电话"].field_type == TYPE_PHONE
    assert by_name["保险员工号"].field_type == "number"


def _resolve_lib_path(tmp_path) -> str:
    """兼容两种运行方式：pytest 的 tmp_path（目录）与脚本模式传入的 .yaml 路径。"""
    p = str(tmp_path)
    return p if p.endswith(".yaml") else os.path.join(p, "property_library.yaml")


def test_library_upsert_and_all(tmp_path):
    lib = PropertyDefLibrary(_resolve_lib_path(tmp_path))

    base = len(lib.all_defs())
    assert base > 0  # 内置属性已存在

    # 新增自定义属性
    lib.upsert(PropertyDef(
        name="就读年级", source="Excel名单", field_type=TYPE_SELECT,
        options=["一年级", "二年级"],
    ))
    assert len(lib.all_defs()) == base + 1
    assert lib.get("就读年级").options == ["一年级", "二年级"]

    # 覆盖内置属性（性别加「其他」选项）
    lib.upsert(PropertyDef(
        name="性别", source="身份证OCR", field_type=TYPE_SELECT,
        options=["男", "女", "其他"],
    ))
    assert len(lib.all_defs()) == base + 1  # 覆盖不增加总数
    assert lib.get("性别").options == ["男", "女", "其他"]
    assert not lib.is_builtin("性别")
    assert lib.is_builtin("学生姓名")


def test_library_persist_and_reload(tmp_path):
    path = _resolve_lib_path(tmp_path)
    lib = PropertyDefLibrary(path)
    lib.upsert(PropertyDef(name="医保类型", source="自定义",
                           field_type=TYPE_SELECT, options=["居民", "职工"]))

    lib2 = PropertyDefLibrary(path)
    assert lib2.get("医保类型") is not None
    assert lib2.get("医保类型").options == ["居民", "职工"]


def test_library_remove(tmp_path):
    lib = PropertyDefLibrary(_resolve_lib_path(tmp_path))
    lib.upsert(PropertyDef(name="临时属性"))

    assert lib.remove("临时属性") is True
    assert lib.get("临时属性") is None
    # 内置属性没有用户定义可删
    assert lib.remove("学生姓名") is False
    assert lib.get("学生姓名") is not None


def test_library_validation(tmp_path):
    lib = PropertyDefLibrary(_resolve_lib_path(tmp_path))

    try:
        lib.upsert(PropertyDef(name=""))
        assert False, "空属性名应被拒绝"
    except ValueError:
        pass

    try:
        lib.upsert(PropertyDef(name="下拉无选项", field_type=TYPE_SELECT, options=[]))
        assert False, "select 无选项应被拒绝"
    except ValueError:
        pass


if __name__ == "__main__":
    import tempfile
    test_property_def_roundtrip()
    test_builtin_defs_cover_all_sources()
    with tempfile.TemporaryDirectory() as td:
        p = lambda name: os.path.join(td, name)
        test_library_upsert_and_all(p("a.yaml"))
        test_library_persist_and_reload(p("b.yaml"))
        test_library_remove(p("c.yaml"))
        test_library_validation(p("d.yaml"))
    print("test_property_def.py 全部通过 ✓")
