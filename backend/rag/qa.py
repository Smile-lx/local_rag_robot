"""RAG 问答编排（design.md『RAG 问答管道』）：检索 → 提示词 → 生成 → 落库。

answer 一站式完成::

    vectorstore.search(question, k=RAG_TOP_K)
      ├─ 无命中 或 最高分 < RAG_MIN_SCORE → mode="refusal"
      │     （固定文案，不编造 SPD 事实，sources 为空）
      └─ 命中 → 组装提示词（资料块带 [来源: 文档名#切片号] 前缀）→ llm_client.chat
            ├─ 成功 → mode="llm"
            └─ 未配置/失败 → mode="retrieval_only"
                  （"（未接入生成模型，…）" + top 片段拼接，各带 [来源] 标注）

无论何种 mode，question 与 answer（assistant 消息含 sources_json）都经
chat_store 落库；会话不存在自动创建，label 仅在新建会话时生效。

多轮上下文（T9，2026-10-25 用户追加"每次请求进行三次对话回顾"）：
``conversation_id`` 有效且该会话已有历史时——

- 检索侧（T9 修复轮第 2 轮，防负优化）：**强信号守卫**——当前问题单独
  检索 top1 ≥ ``RAG_MIN_SCORE``（能独立作答）时直接采用原问结果，上下文
  不进检索（纯文本拼接会稀释语义，实测把强信号 top1 从 0.6813 挤到第
  3、弱信号甚至跌出 top4）；**弱信号救援**——原问 top1 低于阈值（按现状
  会拒答）才以"当前问题 + 最近 1 条历史用户问题"拼接查询再检索一次，
  与原问结果**双查询融合**：按 (doc_id, seq) 去重取较高分后取前 k，融合
  池 ⊇ 原问池 ⇒ 前 k 逐位分数与 top1 分数单调不降（天然满足"增强后
  不劣于未增强"）；融合后 top1 仍低于阈值则照常拒答（与现状等同）；
- 生成侧：提示词在资料块与问题之间注入最近 3 轮对话（单条截断 200 字、
  区块总量封顶 1200 字），系统提示追加"结合历史对话理解用户的追问"；
- 历史**先于本次 question 落库**获取（recent_turns 调用时机约定），当前
  问题不会重复计入；无 conversation_id / 会话不存在 / 首问（无历史）时
  行为与单轮版完全一致，RESTful 契约与各 mode/verified 路径均不受影响。

可测试性约定：vectorstore / llm_client / chat_store 均以模块对象引用
（``from . import xxx`` 后 ``xxx.fn(...)`` 调用），monkeypatch 各模块的
函数即可注入替身；RAG_TOP_K / RAG_MIN_SCORE 亦在调用时读 ``config``。

接口（冻结，供 api/routes_chat 使用）::

    answer(question, conversation_id=None, label=None) -> ChatResult
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .. import config
from . import chat_store, llm_client, vectorstore
from . import verify

__all__ = ["ChatResult", "answer", "SYSTEM_PROMPT", "REFUSAL_ANSWER"]

logger = logging.getLogger(__name__)

#: 系统提示词（design.md 原文，冻结）
SYSTEM_PROMPT: str = (
    "你是医院 SPD 耗材智能客服，只依据给定资料回答，引用资料时标注"
    " [来源: 文档名#切片号]；资料不足时明确说明，不要编造"
)

#: 拒答固定文案（检索无命中或最高分低于阈值时不编造 SPD 事实）
REFUSAL_ANSWER: str = (
    "知识库中未找到相关内容，请尝试换一种问法，或先在知识管理页录入相关文档。"
)

#: retrieval_only 降级标注前缀（design.md 原文）
RETRIEVAL_ONLY_PREFIX: str = "（未接入生成模型，以下为检索到的知识库原文）"

# ------------------------------------------------ 多轮上下文（T9，2026-10-25）
#: 回顾的对话轮数（一组 = 一条 user + 一条 assistant 消息）
CONTEXT_TURNS: int = 3
#: 救援拼接时历史用户问题条数：只取最近 1 条（指代先行词几乎总在上一问，
#: 多拼历史问题只会把旧话题词毒化进救援查询，T9 修复轮由 2 收紧为 1）
CONTEXT_QUERY_QUESTIONS: int = 1
#: 检索拼接时单条历史问题的截断长度（字符）
CONTEXT_QUERY_CHAR_LIMIT: int = 50
#: 检索查询整体长度上限（字符；仅拼接发生后生效，长首问保持原样）
CONTEXT_QUERY_TOTAL_LIMIT: int = 300
#: 提示词注入的历史消息条数上限（3 轮 × user/assistant 各一条 = 6 条）
CONTEXT_HISTORY_MAX_MESSAGES: int = 6
#: 提示词注入时单条历史消息的截断长度（字符，约 200 字）
CONTEXT_HISTORY_MSG_CHAR_LIMIT: int = 200
#: 提示词历史区块的总长上限（字符）
CONTEXT_HISTORY_TOTAL_CHAR_LIMIT: int = 1200
#: 有历史对话时追加到系统提示的追问理解指令（首问不加，系统提示保持原文）
CONTEXT_SYSTEM_SUFFIX: str = "；结合历史对话理解用户的追问"


@dataclass
class ChatResult:
    """一次问答的结果（POST /api/chat 响应体的数据来源）。

    Attributes:
        answer: 最终答案文本（llm 回答 / 拒答文案 / 检索原文拼接）。
        sources: 来源列表 ``[{doc_id,doc_title,seq,section_path,score,snippet}]``，
            refusal 时为空列表；snippet 为切片全文。
        mode: "llm" | "retrieval_only" | "refusal"。
        conversation_id: 本次问答归属的会话 id（既有或自动新建）。
    """

    answer: str
    sources: list[dict[str, Any]]
    mode: str
    conversation_id: int
    model: str = ""
    verified: str = "skipped"


def answer(
    question: str,
    conversation_id: int | None = None,
    label: str | None = None,
) -> ChatResult:
    """回答一个问题：检索 → 生成/降级/拒答 → 落库（多轮时先取历史上下文）。

    Args:
        question: 用户问题（非空校验由 API 层负责，本层不再重复拦截）。
        conversation_id: 既有会话 id；None 或会话不存在时自动新建。
            有效且已有历史时启用 T9 多轮上下文（检索查询拼接 + 提示词注入
            最近三轮，见模块 docstring）。
        label: 会话展示名（科室/姓名），仅新建会话时生效。

    Returns:
        ChatResult(answer, sources, mode, conversation_id, model, verified)。
    """
    # T9 多轮上下文：必须在本次 question 落库之前取历史（chat_store.recent_turns
    # 的调用时机约定），当前问题不会重复计入；会话不存在/未传时无历史，
    # 行为与单轮版完全一致
    history: list[dict[str, Any]] = []
    if conversation_id is not None and chat_store.conversation_exists(conversation_id):
        history = chat_store.recent_turns(conversation_id, turns=CONTEXT_TURNS)
        if history:
            logger.info(
                "多轮上下文: 会话 %s 注入 %s 条历史消息", conversation_id, len(history)
            )
    hits: list[vectorstore.Hit]
    if history:
        hits = _search_with_context(question, history)
    else:
        # 无历史：原问直检，与单轮版路径完全一致
        hits = vectorstore.search(question, k=config.RAG_TOP_K)
    # search 契约保证按 score 降序，hits[0] 即最高分
    if not hits or hits[0].score < config.RAG_MIN_SCORE:
        mode = "refusal"
        answer_text = REFUSAL_ANSWER
        used_model = ""
        verified = "skipped"
        sources: list[dict[str, Any]] = []
        logger.info(
            "拒答: 命中 %s 条, 最高分 %.4f, 阈值 %.4f",
            len(hits),
            hits[0].score if hits else 0.0,
            config.RAG_MIN_SCORE,
        )
    else:
        sources = [_hit_to_source(hit) for hit in hits]
        llm_result = llm_client.chat(_build_messages(question, hits, history))
        if llm_result.mode == "llm":
            if not config.VERIFY_ANSWER:
                mode = "llm"
                answer_text = llm_result.answer
                used_model = getattr(llm_result, "model", "")
                verified = "skipped"
            else:
                # 防幻觉多重校验（2026-10-25）：忠实度/引用真实性/数字一致性
                # 三层对照检索原文校验；fail → 弃用生成答案降级仅检索
                verification = verify.verify_answer(llm_result.answer, hits)
                if verification.verdict == "fail":
                    logger.warning(
                        "防幻觉校验不通过，弃用生成答案降级仅检索: %s",
                        verification.summary(),
                    )
                    mode = "retrieval_only"
                    used_model = ""
                    verified = "fail"
                    answer_text = "\n\n".join(
                        [RETRIEVAL_ONLY_PREFIX] + [_format_hit(hit) for hit in hits]
                    )
                else:
                    mode = "llm"
                    answer_text = llm_result.answer
                    used_model = getattr(llm_result, "model", "")
                    verified = verification.verdict
                    if verified == "suspect":
                        logger.warning(
                            "防幻觉校验疑似（返回但已标注）: %s", verification.summary()
                        )
        else:
            mode = "retrieval_only"
            used_model = ""
            verified = "skipped"
            answer_text = "\n\n".join(
                [RETRIEVAL_ONLY_PREFIX] + [_format_hit(hit) for hit in hits]
            )
        logger.info("问答完成: mode=%s, 命中 %s 条", mode, len(hits))

    # 无论何种 mode 都落库：会话不存在自动创建，user/assistant 各一条
    conv_id = chat_store.ensure_conversation(conversation_id, label=label)
    chat_store.add_message(conv_id, "user", question)
    chat_store.add_message(conv_id, "assistant", answer_text, sources=sources)
    return ChatResult(
        answer=answer_text,
        sources=sources,
        mode=mode,
        conversation_id=conv_id,
        model=used_model,
        verified=verified,
    )


def _build_messages(
    question: str,
    hits: list[vectorstore.Hit],
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    """组装 OpenAI / ollama 通用的 system+user 消息（资料块带 [来源] 前缀）。

    history 非空（多轮追问）时：user 消息在资料块与问题之间注入最近三轮
    对话区块，system 提示追加"结合历史对话理解用户的追问"；history 为
    None/空时输出与单轮版逐字节一致（首问行为不变）。
    """
    context = "\n\n".join(_format_hit(hit) for hit in hits)
    system = SYSTEM_PROMPT if not history else SYSTEM_PROMPT + CONTEXT_SYSTEM_SUFFIX
    history_block = _format_history(history) if history else ""
    user_content = f"资料：\n{context}\n\n{history_block}问题：{question}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]


def _search_with_context(
    question: str, history: list[dict[str, Any]]
) -> list[vectorstore.Hit]:
    """带多轮上下文的检索（T9 修复轮）：强信号守卫 + 弱信号双查询融合。

    - **强信号守卫**：当前问题单独检索 top1 ≥ ``RAG_MIN_SCORE``（能独立
      作答）→ 直接返回原问结果，上下文不进检索——纯文本拼接实测会把强
      信号命中挤出 top1（负优化），此处从根上杜绝：增强后的结果与未增强
      完全一致，"不得劣于未增强"不变式平凡成立；上下文仅注入生成侧提示词；
    - **弱信号救援**：原问 top1 低于阈值（无上下文时本会拒答）→ 以拼接
      查询（``_augment_query``）再检索一次，与原问结果融合（``_fuse_hits``）。
      救援后 top1 过阈值则作答，仍不过则照常拒答——与现状等同，任何情况
      下不劣于未增强。
    """
    k = config.RAG_TOP_K
    base = vectorstore.search(question, k=k)
    if base and base[0].score >= config.RAG_MIN_SCORE:
        logger.debug(
            "强信号守卫: 原问 top1 %.4f ≥ 阈值 %.4f，跳过上下文检索增强",
            base[0].score,
            config.RAG_MIN_SCORE,
        )
        return base
    rescue = vectorstore.search(_augment_query(question, history), k=k)
    fused = _fuse_hits(base, rescue, k)
    logger.info(
        "弱信号救援: 原问 top1 %.4f < 阈值 %.4f，融合后 top1 %.4f"
        "（原问 %s 条 / 拼接 %s 条 / 融合 %s 条）",
        base[0].score if base else 0.0,
        config.RAG_MIN_SCORE,
        fused[0].score if fused else 0.0,
        len(base),
        len(rescue),
        len(fused),
    )
    return fused


def _fuse_hits(
    base: list[vectorstore.Hit], rescue: list[vectorstore.Hit], k: int
) -> list[vectorstore.Hit]:
    """双查询结果融合：按 (doc_id, seq) 去重取较高分，按分数降序取前 k。

    PM 修复方向（"同 doc:seq 取 max"）的朴素 max 融合：融合池 ⊇ 原问池
    ⇒ 前 k 逐位分数与 top1 分数相对原问结果**单调不降**（天然满足防负
    优化）；同一切片两侧都出现时保留较高分（上下文给原问命中抬分，正是
    弱信号救援的主要机制——目标切片两侧都在、救援侧分数越过阈值）。
    强信号 top1 不被挤出由 ``_search_with_context`` 的强信号守卫兜底
    （强信号根本不进融合）。
    """
    best: dict[tuple[int, int], vectorstore.Hit] = {}
    for hit in list(base) + list(rescue):
        key = (hit.doc_id, hit.seq)
        current = best.get(key)
        if current is None or hit.score > current.score:
            best[key] = hit
    return sorted(best.values(), key=lambda hit: hit.score, reverse=True)[:k]


def _augment_query(question: str, history: list[dict[str, Any]]) -> str:
    """弱信号救援查询：当前问题 + 最近 1 条历史用户问题（T9 修复轮）。

    仅在 ``_search_with_context`` 的弱信号分支（原问 top1 低于阈值）使用。
    取最近 ``CONTEXT_QUERY_QUESTIONS``（1）条历史用户问题、跳过与已拼接
    文本重复的问题（用户连发同一问时避免 query 重复膨胀），单条截断
    ``CONTEXT_QUERY_CHAR_LIMIT``（50）字，拼接结果整体不超过
    ``CONTEXT_QUERY_TOTAL_LIMIT``（300）字。只拼最近 1 条的理由：指代词
    （"它 / 其 / 该"）的先行词几乎总在上一问，更早的问题多为话题漂移，
    拼入只会把旧话题词毒化进救援查询（实测拼接后弱信号目标跌出 top4）。

    无历史或历史用户问题全部与当前问题重复时**原样返回 question**
    （含超长首问）。
    """
    if not history:
        return question
    parts: list[str] = [question.strip()]
    seen = {question.strip()}
    appended = 0
    historical_users = [
        str(message.get("content", ""))
        for message in history
        if str(message.get("role", "")) == "user"
    ]
    for past in reversed(historical_users):  # 最新在前，只取最近 1~2 条
        if appended >= CONTEXT_QUERY_QUESTIONS:
            break
        text = past.strip()
        if not text or text in seen:
            continue
        parts.append(text[:CONTEXT_QUERY_CHAR_LIMIT])
        seen.add(text)
        appended += 1
    if len(parts) == 1:
        return question
    return " ".join(parts)[:CONTEXT_QUERY_TOTAL_LIMIT]


def _format_history(history: list[dict[str, Any]]) -> str:
    """把最近三轮对话格式化为提示词历史区块（升序，双重截断不抛错）。

    单条消息截断到 ``CONTEXT_HISTORY_MSG_CHAR_LIMIT``（200）字，区块整体
    截断到 ``CONTEXT_HISTORY_TOTAL_CHAR_LIMIT``（1200）字；区块头部显式
    声明历史仅供理解追问指代、不得作为新的事实来源，防止模型把历史当
    资料引用（防幻觉边界与 verify 层一致：唯一事实依据仍是检索原文）。
    """
    speakers = {"user": "用户", "assistant": "客服"}
    lines = []
    for message in history[:CONTEXT_HISTORY_MAX_MESSAGES]:
        role = speakers.get(str(message.get("role", "")), "用户")
        content = _clip(str(message.get("content", "")), CONTEXT_HISTORY_MSG_CHAR_LIMIT)
        lines.append(f"{role}：{content}")
    header = "最近对话（仅供理解用户的追问指代，不要作为新的事实来源）："
    block = header + "\n" + "\n".join(lines)
    if len(block) > CONTEXT_HISTORY_TOTAL_CHAR_LIMIT:
        block = block[:CONTEXT_HISTORY_TOTAL_CHAR_LIMIT]
    return block + "\n\n"


def _clip(text: str, limit: int) -> str:
    """按字符数截断，超长以省略号收尾（截断不抛错）。"""
    return text if len(text) <= limit else text[:limit] + "…"


def _hit_to_source(hit: vectorstore.Hit) -> dict[str, Any]:
    """把检索命中转换为契约 sources 结构（snippet 为切片全文）。"""
    return {
        "doc_id": hit.doc_id,
        "doc_title": hit.doc_title,
        "seq": hit.seq,
        "section_path": hit.section_path,
        "score": hit.score,
        "snippet": hit.text,
    }


def _format_hit(hit: vectorstore.Hit) -> str:
    """把检索命中格式化为带来源标注的原文块。"""
    return f"[来源: {hit.doc_title}#{hit.seq}]\n{hit.text}"
