"""防幻觉多重校验（2026-10-25 加固，用户点单"多重校验解决 AI 幻觉"）。

对大模型生成的答案做三层校验，全部只对照**本次检索命中的原文**，不引入
任何外部知识：

1. 忠实度校验（grounding）：把答案按句切分，逐句计算与检索原文的
   字符二元组（bigram）覆盖率；覆盖率过低的句子视为"无依据"，
   无依据字符占比过高 → 判 fail；
2. 引用真实性校验（citation）：答案中 "[来源: 文档名#切片号]" 标注必须
   与本次命中集合逐一对应——模型编造的出处直接判 fail；
3. 数字一致性校验（numbers）：答案中出现的阿拉伯数字（价格/天数/剂量
   等幻觉高危项）必须能在检索原文中找到，找不到记 mismatch → suspect。

判定与处置（qa 层执行）：

- ``pass``：全部通过，正常以 mode="llm" 返回；
- ``suspect``：整体有依据但个别数字对不上，返回但经 ``verified`` 字段
  标注，供前端/审计提示人工核对；
- ``fail``：忠实度不足或引用造假 → **自动降级为仅检索**（返回原文
  片段，弃用该生成答案），宁返回原文不出幻觉答案。

阈值经 ``.env`` 可调：``FAITH_MIN``（默认 0.5，低于判 fail）、
``FAITH_SUSPECT``（默认 0.8，低于判 suspect）；``VERIFY_ANSWER=0``
可整体关闭（默认开启，医疗场景务必保持开启）。

接口::

    verify_answer(answer: str, hits: list[vectorstore.Hit]) -> VerifyResult
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .. import config

__all__ = ["VerifyResult", "verify_answer"]

logger = logging.getLogger(__name__)

#: 句切分（中文句读 + 换行）；短于该长度的连接性短句不参与忠实度统计
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")
_MIN_SENTENCE_CHARS = 6
#: 引用标注（与 qa._format_hit / SYSTEM_PROMPT 的 "[来源: 文档名#切片号]" 对应）
_CITATION_RE = re.compile(r"\[来源[:：]\s*([^\]#]+)#(\d+)\s*\]")
#: 阿拉伯数字（整数或小数）；中文数字不在此列（原文常为中文数字，强校验会误伤）
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
#: bigram 提取前去掉的空白与普通标点（保留字母数字与 CJK）
_STRIP_RE = re.compile(r"[\s，。、；：？！,.;:!?()（）\[\]【】\"'“”‘’]+")

#: 忠实度句子级通过线：单句 bigram 覆盖率达到此值视为有依据
_SENTENCE_GROUNDED_AT = 0.5


@dataclass
class VerifyResult:
    """一次防幻觉校验的结果。"""

    verdict: str = "pass"  # "pass" | "suspect" | "fail"
    grounded_ratio: float = 1.0  # 有依据字符占可评分字符比例
    fake_citations: list[str] = field(default_factory=list)
    number_mismatches: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"grounded={self.grounded_ratio:.2f}"]
        if self.fake_citations:
            parts.append(f"fake_citations={self.fake_citations}")
        if self.number_mismatches:
            parts.append(f"number_mismatches={self.number_mismatches}")
        return ", ".join(parts)


def _bigrams(text: str) -> set[str]:
    """归一化后取字符二元组（CJK/字母数字参与，标点空白剔除）。"""
    norm = _STRIP_RE.sub("", text)
    return {norm[i : i + 2] for i in range(len(norm) - 1)}


def _score_sentences(answer: str, source_grams: set[str]) -> float:
    """逐句计算忠实度：有依据句的字符量 / 可评分句总字符量。

    无可评分句（答案极短或全是连接词）视为 1.0（无从证伪）。
    """
    total = grounded = 0
    for raw in _SENTENCE_SPLIT.split(answer):
        sentence = raw.strip()
        if len(sentence) < _MIN_SENTENCE_CHARS:
            continue
        total += len(sentence)
        grams = _bigrams(sentence)
        if not grams:
            continue
        coverage = len(grams & source_grams) / len(grams)
        if coverage >= _SENTENCE_GROUNDED_AT:
            grounded += len(sentence)
    return 1.0 if total == 0 else grounded / total


def verify_answer(answer: str, hits: list) -> VerifyResult:
    """对生成答案执行三层校验（忠实度 / 引用真实性 / 数字一致性）。

    Args:
        answer: 大模型生成的答案文本（含 [来源] 标注）。
        hits: 本次检索命中的 ``vectorstore.Hit`` 列表（唯一事实依据）。

    Returns:
        VerifyResult；verdict 为 "pass" / "suspect" / "fail"，
        fail 时 qa 层应弃用该答案降级为仅检索。
    """
    result = VerifyResult()
    if not answer or not answer.strip():
        return result  # 空答案交由上游处理，无从校验

    source_text = "\n".join(getattr(hit, "text", "") for hit in hits)
    source_grams = _bigrams(source_text)

    # 层 2：引用真实性（标题与切片号必须与命中集合完全对应）
    valid_pairs = {
        (getattr(hit, "doc_title", ""), int(getattr(hit, "seq", -1))) for hit in hits
    }
    for m in _CITATION_RE.finditer(answer):
        pair = (m.group(1).strip(), int(m.group(2)))
        if pair not in valid_pairs:
            result.fake_citations.append(m.group(0))

    # 引用标注是元数据而非陈述内容：忠实度与数字校验均在剥离标注后的正文上进行
    answer_body = _CITATION_RE.sub("", answer)

    # 层 1：忠实度
    result.grounded_ratio = _score_sentences(answer_body, source_grams)

    # 层 3：数字一致性（正文中的阿拉伯数字须在原文出现）
    for m in _NUMBER_RE.finditer(answer_body):
        if m.group(0) not in source_text:
            result.number_mismatches.append(m.group(0))

    if result.grounded_ratio < config.FAITH_MIN or result.fake_citations:
        result.verdict = "fail"
    elif result.grounded_ratio < config.FAITH_SUSPECT or result.number_mismatches:
        result.verdict = "suspect"
    else:
        result.verdict = "pass"
    return result
