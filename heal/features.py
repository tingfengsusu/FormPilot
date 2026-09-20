"""故障特征提取与相似度（设计文档第三节「特征归一化」）。

**为什么要有 signature**：错误文本里混着大量「具体值」——目标日期、locator
路径、超时毫秒数、URL 参数。同一个故障每次出现，这些值都不一样；如果拿原文
比相似度，命中率会很差。所以先把具体值抹掉，只留「形状」：

    「日期选择器结构不识别或日期不可选（目标 2026-09-20）」
        → 「日期选择器结构不识别或日期不可选 目标 #-#-#」

**相似度**用两路取较大值（MVP 零依赖、可离线）：

- 中文 2-gram + 英文/数字词的 **token 重合**（对短句、关键词敏感）
- ``difflib.SequenceMatcher`` 比率（对长句、语序变化更宽容）

P2 换向量检索时，只需替换 :mod:`heal.store` 里的 ``find_similar`` 实现，
本模块的签名逻辑仍然有用（可以一起喂给向量模型）。
"""
from __future__ import annotations

import difflib
import re
from typing import List, Set

from .models import FailureFeature

#: 具体值 → 占位符（顺序敏感：先处理长的）
_PATTERNS = (
    (re.compile(r"https?://\S+"), " url "),
    (re.compile(r"[A-Za-z]:\\[^\s'\"]+"), " path "),
    (re.compile(r"\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?"), " date "),
    (re.compile(r"\d{4}-\d{1,2}-\d{1,2}T\d{2}:\d{2}(:\d{2})?"), " datetime "),
    (re.compile(r"#[A-Za-z0-9_-]+"), " id "),        # #app / #id
    (re.compile(r"[0-9a-f]{8,}"), " hex "),          # 哈希/长 id
    (re.compile(r"\d+\.\d+"), " num "),              # 浮点
    (re.compile(r"\d+"), " # "),                     # 整数（超时毫秒、目标日期等）
    (re.compile(r"'[^']*'"), " quoted "),            # 选择器片段
    (re.compile(r'"[^"]*"'), " quoted "),
)

_FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
    "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"
    "（）【】《》，。：；！？　",
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "()[]<>,.:;!? ",
)


def normalize_symptom(text: str) -> str:
    """归一化：全角转半角、压空白、小写（便于比较）。"""
    if not text:
        return ""
    value = str(text).translate(_FULLWIDTH).lower()
    value = re.sub(r"[\s\u3000]+", " ", value)
    return value.strip()


def signature_of(text: str) -> str:
    """症状指纹：归一化 + 抹掉具体值（日期/数字/路径/URL/引号内容）。"""
    value = normalize_symptom(text)
    for pattern, replacement in _PATTERNS:
        value = pattern.sub(replacement, value)
    # 只清标点，**保留占位词**（date / path / url …）——它们就是「形状」本身，
    # 保留了才能让「目标 2026-09-20」和「目标 2026-10-05」得到同一个签名
    value = re.sub(r"[()\[\]{}<>:;,.]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def extract_feature(error: str, step: str = "", property_name: str = "",
                    variety: str = "") -> FailureFeature:
    """把一次失败变成结构化特征（签名自动生成）。"""
    symptom = str(error or "").strip()
    return FailureFeature(symptom=symptom, step=str(step or ""),
                          property_name=str(property_name or ""),
                          variety=str(variety or ""),
                          signature=signature_of(symptom))


# ----------------------------------------------------------------------
# 相似度
# ----------------------------------------------------------------------
_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9_]+")


def _tokens(text: str) -> Set[str]:
    """中文 2-gram + 英文/数字整词（中文没有空格，只能按 n-gram 切）。"""
    tokens: Set[str] = set(_WORD.findall(text))
    cjk = "".join(_CJK.findall(text))
    for index in range(len(cjk) - 1):
        tokens.add(cjk[index:index + 2])
    if len(cjk) == 1:
        tokens.add(cjk)
    return tokens


def similarity(left: str, right: str) -> float:
    """两段文本的相似度（0~1）：token 重合与序列比率的加权较大者。"""
    a, b = normalize_symptom(left), normalize_symptom(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    union = len(tokens_a | tokens_b)
    jaccard = len(tokens_a & tokens_b) / union if union else 0.0
    overlap = (len(tokens_a & tokens_b) / min(len(tokens_a), len(tokens_b))
               if tokens_a and tokens_b else 0.0)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # 短句靠重合度、长句靠序列比率；都取上界再取最大，宁可多命中一点
    return round(max(jaccard, overlap * 0.85, ratio * 0.9), 4)


def feature_similarity(feature: FailureFeature, case) -> float:
    """特征与案例的相似度（症状原文与签名各比一次，品种一致再加一点分）。"""
    scores = [
        similarity(feature.symptom, getattr(case, "symptom", "")),
        similarity(feature.signature or signature_of(feature.symptom),
                   getattr(case, "signature", "")),
    ]
    # 属性/步骤一致说明"同一个字段的同一类问题"，给小幅加分
    if feature.property_name and feature.property_name == getattr(
                case, "property_name", ""):
        scores.append(min(1.0, max(scores) + 0.05))
    if feature.step and feature.step == getattr(case, "step", ""):
        scores.append(min(1.0, max(scores) + 0.03))
    return round(max(scores), 4)


__all__ = ["normalize_symptom", "signature_of", "extract_feature",
           "similarity", "feature_similarity"]
