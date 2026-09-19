"""batch/dictionaries.py 测试 —— 归一化 + 别名匹配 + 未命中不猜。

核心约束（设计文档附录 C.1/C.3）：
    权威值 = 页面下拉选项（options_*.yaml，探针产出）
    台账写法 → 归一化 → 选项直接命中 / 别名表命中 → 必须落在选项集合内
    命中多个 → 歧义；未命中 → 数据异常，**绝不猜一个相近值**

覆盖：
  - normalize / strip_noise 的口径（全角、空白）
  - 选项直接命中、别名命中（目标值在选项内）
  - 未命中不猜、歧义不猜
  - 别名目标值必须来自页面选项（配置层硬规则，防止「自行拟定」的目标值）
  - 选项尚未探针产出时**不回退**到别名表权威（过渡模式已废弃）
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batch.dictionaries import (KIND_TEXT, MATCH_ALIAS,
                                MATCH_ALIAS_OUT_OF_OPTIONS, MATCH_AMBIGUOUS,
                                MATCH_EMPTY, MATCH_EXACT, MATCH_UNMATCHED,
                                Dictionary, DictionarySet, load_dictionary_set,
                                normalize, strip_noise)

CONFIG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")


def _school_dict(**kwargs) -> Dictionary:
    """一份探针已产出的样例字典（虚构选项，与真实页面无关）。"""
    options = kwargs.pop("options", ["柳集小学", "柳集中学", "实验小学"])
    aliases = kwargs.pop("aliases", {"柳小": "柳集小学"})
    return Dictionary("school", options=options, aliases=aliases, **kwargs)


# ----------------------------------------------------------------------
# 归一化
# ----------------------------------------------------------------------
def test_normalize():
    assert normalize(" 柳 集 小 学 ") == "柳集小学"
    assert normalize("柳\u3000集") == "柳集"          # 全角空格
    assert normalize("ＡＢＣ１２３") == "ABC123"      # 全角字母数字
    assert normalize("（１）班") == "(1)班"           # 全角括号
    assert normalize(None) == ""
    assert normalize("") == ""
    # 只做形态归一，不做语义展开（"柳小" → "柳集小学" 交给别名表）
    assert normalize("柳小") == "柳小"


def test_strip_noise():
    assert strip_noise("洛阳市实验小学") == "实验"
    assert strip_noise("柳集小学") == "柳集"
    assert strip_noise("柳集") == "柳集"              # 剥完为空则保留原值


# ----------------------------------------------------------------------
# 匹配
# ----------------------------------------------------------------------
def test_exact_option_match():
    result = _school_dict().match("柳集小学")
    assert result.ok and result.status == MATCH_EXACT
    assert result.canonical == "柳集小学"
    # 归一化后命中（空格/全角不同也算命中）
    assert _school_dict().match(" 柳 集 小 学 ").canonical == "柳集小学"


def test_alias_match_target_in_options():
    result = _school_dict().match("柳小")
    assert result.ok and result.status == MATCH_ALIAS
    assert result.canonical == "柳集小学"


def test_alias_target_in_options_resolves_to_option_text():
    """别名目标值归一化后命中选项 → 用**选项原文**作为权威值。"""
    dictionary = Dictionary("school", options=["柳集小学"],
                            aliases={"柳小": "柳集小学　"})   # 目标值带全角空格
    result = dictionary.match("柳小")
    assert result.ok and result.status == MATCH_ALIAS
    assert result.canonical == "柳集小学"     # 用页面原文，不用别名表里的写法


def test_alias_target_out_of_options_rejected():
    """别名目标值不在选项集合内 → 数据异常（这就是「过渡模式」被关掉的地方）。"""
    dictionary = Dictionary("school", options=["柳集小学"],
                            aliases={"柳小": "柳集完全小学"})
    result = dictionary.match("柳小")
    assert not result.ok
    assert result.status == MATCH_ALIAS_OUT_OF_OPTIONS
    assert "不在页面选项" in result.detail

    # 目标值在选项里对应多个选项 → 歧义，不猜
    ambiguous = Dictionary("class_name", options=["1班", "１班"],
                           aliases={"一班": "1班"})
    result2 = ambiguous.match("一班")
    assert not result2.ok and result2.status == MATCH_AMBIGUOUS


def test_text_field_mode():
    """文本输入字段（实测：学校名称没有下拉）：权威值 = 人工确认的规范名。

    没有选项集合可核对，但**仍然不猜**：没映射的写法照样数据异常。
    """
    dictionary = Dictionary("school", kind=KIND_TEXT,
                            aliases={"柳小": "柳集小学"})
    assert dictionary.is_text_field is True
    assert dictionary.options_ready is True          # 文本字段无需选项
    assert dictionary.authority_values == ["柳集小学"]

    hit = dictionary.match("柳小")
    assert hit.ok and hit.status == MATCH_ALIAS
    assert hit.canonical == "柳集小学"
    assert "人工确认" in hit.detail

    miss = dictionary.match("清花园")
    assert not miss.ok and miss.status == MATCH_UNMATCHED
    assert "文本输入" in miss.detail and "不猜" in miss.detail


def test_text_field_accepts_already_canonical_value():
    """规范名本身也要能通过（导入时已映射过一次，校验层会再匹配一次）。"""
    dictionary = Dictionary("school", kind=KIND_TEXT,
                            aliases={"柳小": "柳泉小学"})
    result = dictionary.match("柳泉小学")
    assert result.ok and result.status == MATCH_EXACT
    assert "已是人工确认的规范名" in result.detail


def test_unmatched_never_guesses():
    """台账值不在选项/别名表里 → 数据异常，不返回任何"最相近"的值。"""
    result = _school_dict().match("清花园")
    assert not result.ok
    assert result.status == MATCH_UNMATCHED
    assert result.canonical == ""
    assert "清花园" in result.detail


def test_empty_value():
    result = _school_dict().match("")
    assert not result.ok and result.status == MATCH_EMPTY


def test_ambiguous_options():
    """同一归一化值对应多个选项 → 歧义，不猜。"""
    dictionary = Dictionary("class_name", options=["1班", "１班"],
                            aliases={"一班": "1班"})
    result = dictionary.match("1班")
    assert not result.ok and result.status == MATCH_AMBIGUOUS
    assert len(result.candidates) == 2


def test_strip_noise_optional():
    raw = "洛阳市实验小学"
    # 默认关闭：带噪声的值不命中（不猜）
    assert not _school_dict(options=["实验"]).match(raw).ok
    # 显式开启后按配置剥噪声
    dictionary = _school_dict(options=["实验"], strip_noise_enabled=True)
    assert dictionary.match(raw).canonical == "实验"


def test_contains_hard_rule():
    dictionary = _school_dict()
    assert dictionary.contains("柳集小学") is True
    assert dictionary.contains("柳集小学 ") is True     # 归一化后比较
    assert dictionary.contains("某不存在的学校") is False


def test_dictionary_set_unknown_key_passthrough():
    """没有对应字典的字段按原值使用（不误判为异常）。"""
    ds = DictionarySet({"school": _school_dict()})
    result = ds.match("plan", "基础版")
    assert result.ok and result.canonical == "基础版"
    assert ds.match("school", "柳小").canonical == "柳集小学"
    assert not ds.match("school", "清花园").ok


def test_dictionary_set_helpers():
    ds = DictionarySet({"school": _school_dict()})
    assert ds.options_ready is True
    assert ds.missing_options() == []
    assert ds.unmatched("school", ["柳小", "清花园", "实验小学"]) == ["清花园"]

    empty = DictionarySet({"school": Dictionary("school")})
    assert empty.options_ready is False
    assert empty.missing_options() == ["school"]


# ----------------------------------------------------------------------
# 配置层硬规则
# ----------------------------------------------------------------------
def test_config_alias_targets_must_be_real_options():
    """配置层硬规则（按控件类型分）：

    - 下拉字段（年级/班级）：别名目标值必须来自页面选项，不允许「自行拟定」；
      选项尚未探针产出时，别名表也必须是空的——否则这些目标值就是猜的。
    - 文本输入字段（学校名称，实测没有下拉）：没有选项集合，
      规范名只能人工确认（本测试不评价内容，但要求配置里声明成 text）。
    """
    from batch.recipe import Recipe

    ds = Recipe.load().build_dictionaries()      # 走真实配置（含 dictionary.kinds）
    assert ds.get("school").is_text_field is True, \
        "学校名称在投保页上是文本输入，config/batch.yaml 的 dictionary.kinds 应声明为 text"

    problems = []
    for name in ("grade", "class_name"):
        dictionary = ds.get(name)
        assert dictionary.is_text_field is False
        if not dictionary.options:
            if dictionary.aliases:
                problems.append(
                    f"{name}: options_*.yaml 为空，但别名表有 "
                    f"{len(dictionary.aliases)} 条目标值未经选项核对")
            continue
        options = {normalize(o) for o in dictionary.options}
        for key, target in dictionary.aliases.items():
            if normalize(target) not in options:
                problems.append(f"{name}: 「{key}」→「{target}」不在选项集合内")
    assert not problems, "别名表存在未核对的目标值：\n  " + "\n  ".join(problems)


def test_no_transition_mode_when_options_missing():
    """选项未产出时不得回退到「别名表即权威」——必须报未命中。"""
    dictionary = Dictionary("school", options=[], aliases={"柳小": "柳集小学"})
    result = dictionary.match("柳小")
    assert not result.ok
    assert result.status == MATCH_UNMATCHED
    assert "options_school" in (result.detail + result.note) or \
        "探针" in (result.detail + result.note)


if __name__ == "__main__":
    test_normalize()
    test_strip_noise()
    test_exact_option_match()
    test_alias_match_target_in_options()
    test_alias_target_in_options_resolves_to_option_text()
    test_alias_target_out_of_options_rejected()
    test_text_field_mode()
    test_text_field_accepts_already_canonical_value()
    test_unmatched_never_guesses()
    test_empty_value()
    test_ambiguous_options()
    test_strip_noise_optional()
    test_contains_hard_rule()
    test_dictionary_set_unknown_key_passthrough()
    test_dictionary_set_helpers()
    test_config_alias_targets_must_be_real_options()
    test_no_transition_mode_when_options_missing()
    print("test_batch_dictionaries.py 全部通过 ✓")
