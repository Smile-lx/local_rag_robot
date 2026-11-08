"""敏感信息外发二次校验（医疗数据安全闸，2026-10-25 加固）。

在数据离开本机的唯一出口——第 1 级外部 OpenAI 兼容 API——发送前，对
完整消息内容（用户问题 + 检索到的知识片段）做敏感模式扫描；命中任一
模式即拒绝外发，由 ``llm_client`` 自动降级到本机 ollama 或纯检索模式。
本机 ollama 与纯检索不经网络外发，不受此闸限制，服务不中断。

设计边界：

- 模式取保守正则（身份证号 / 手机号 / 银行卡号 / 住院·病历·就诊号），
  策略是"宁可误拦、不可漏放"——误拦只损失外部生成质量（降级本地），
  漏放则敏感数据出院；
- 审计日志只记录模式名与脱敏预览（命中片段前后各 4 字符 + 长度），
  绝不把原文敏感内容写进日志；
- ``.env`` 设 ``SENSITIVE_EGRESS_BLOCK=0`` 可关闭（默认开启；医疗场景
  务必保持开启，见 README 配置说明）。

接口::

    scan(text) -> list[Finding]        # 全部命中（Finding.pattern + 脱敏预览）
    contains_sensitive(text) -> bool   # 任一命中即 True
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

__all__ = ["Finding", "scan", "contains_sensitive"]

logger = logging.getLogger(__name__)

#: 每种模式最多报告的命中数（防大文本刷屏；拦截与否只看有无命中）
_MAX_FINDINGS_PER_PATTERN: int = 3


@dataclass
class Finding:
    """一次敏感命中：模式名 + 脱敏预览（不含原文敏感串）。"""

    pattern: str
    preview: str


#: 敏感模式表：(模式名, 编译正则)。新增模式只需在此追加。
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("身份证号", re.compile(r"\b\d{17}[0-9Xx]\b")),
    ("手机号", re.compile(r"\b1[3-9]\d{9}\b")),
    ("银行卡号", re.compile(r"\b\d{16,19}\b")),
    (
        "住院/病历/就诊号",
        re.compile(
            r"(?:住院号|病历号|就诊号|门诊号|登记号)[:：\s]*[A-Za-z0-9\-]{4,}",
            re.IGNORECASE,
        ),
    ),
]


def _mask_preview(text: str, start: int, end: int) -> str:
    """生成脱敏预览：命中片段前后各 4 字符，中间以 *** 与长度替代。"""
    before = text[max(0, start - 4) : start]
    after = text[end : end + 4]
    return f"{before}***({end - start}字)***{after}"


def scan(text: str) -> list[Finding]:
    """扫描文本，返回全部敏感命中（脱敏预览，不含原文敏感串）。"""
    findings: list[Finding] = []
    for name, pattern in _PATTERNS:
        for i, match in enumerate(pattern.finditer(text)):
            if i >= _MAX_FINDINGS_PER_PATTERN:
                break
            findings.append(
                Finding(pattern=name, preview=_mask_preview(text, match.start(), match.end()))
            )
    return findings


def contains_sensitive(text: str) -> bool:
    """任一敏感模式命中即 True（llm_client 外发闸用，只判有无）。"""
    return any(pattern.search(text) for _, pattern in _PATTERNS)
