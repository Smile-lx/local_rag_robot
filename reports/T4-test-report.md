# 测试报告 T4

结果: PASS

覆盖验收标准: A1 ✓ / A2 ✓ / A3 ✓ / A4 ✓ / A5 ✓

- 测试文件: `tests/test_47846874_T4_qa.py`（9 个用例，另含 3 组补充检查 S1/S2/S3）
- 复现命令（Git Bash，工作目录 `E:\ai\ai`，首轮含模型加载约 15~60s）:
  `export PYTHONIOENCODING=utf-8 && py -3 -m pytest tests/test_47846874_T4_qa.py -v`
- 实测: 9 passed in 15.79s（二次复跑 9 passed in 15.55s，可重复）
- 环境: Windows 11 + Git Bash, Python 3.10.0, pytest 9.1.1；bge-small-zh-v1.5 已缓存本地
- 被测对象: `backend/rag/llm_client.py`、`backend/rag/qa.py`、`backend/rag/chat_store.py`（Coder changed_files 全覆盖）

## 验收逐条结论

| 标准 | 用例 | 结论 |
|---|---|---|
| A1 无 key 无 ollama → retrieval_only + 降级标注 + 检索片段 | test_a1_no_key_no_ollama_retrieval_only | ✓ |
| A2 API 不可达 → 降级不崩溃、耗时受 timeout 约束 | test_a2_unreachable_api_degrades_within_timeout | ✓ |
| A3 无关问题 → refusal + 『知识库中未找到』+ sources 空 | test_a3_unrelated_question_refusal | ✓ |
| A4 monkeypatch chat 固定回答 → llm 模式答案/来源/会话落库 | test_a4_llm_mode_persists_answer_sources_conversation | ✓ |
| A5 chat_store 新建会话/消息追加/message_count 与 GET 契约 | test_a5_chat_store_conversation_and_message_contract、test_a5_ensure_conversation_semantics | ✓ |

## 关键实测数据

- **A1**: `LLM_API_KEY=""` + `OLLAMA_BASE` 指向本地关闭端口（连接必拒绝）→ `mode='retrieval_only'`，answer 以"（未接入生成模型，以下为检索到的知识库原文）"开头、含语料原文片段与 `[来源: 文档名#切片号]` 标注；sources 字段集与 design 契约精确一致（`{doc_id,doc_title,seq,section_path,score,snippet}`）；user+assistant 两条消息均落库。
- **A2**: 本地黑洞 TCP 服务（127.0.0.1 临时端口，accept 后不响应）模拟"不可达 API 地址"，env 设 `LLM_TIMEOUT=2`、`OLLAMA_PROBE_TIMEOUT=0.5` → **实测耗时 2.59s**（= 2.0s 一级读超时 + 0.5s 二级探测超时 + 检索/落库开销），断言区间 `[1.9, 6.0)s` 通过；降级链路 warning 日志确认两级分别恰在 env 配置的超时值上超时（read timeout=2.0 / 0.5），证明 env 超时覆盖端到端生效（若未生效走默认 60s+2s 将远超上限）。
- **A3**: 语料 = 样例 SPD 文档（3 片）+ 行政会议干扰文档（2 片）；无关问题"给我写一首关于夏威夷海岛冲浪的诗"实测 top score **0.2555 < 0.30** → `mode='refusal'`、文案含『知识库中未找到』、sources 为空、固定兜底文案逐字一致、照样落库两条消息。
- **A4**: monkeypatch `qa.llm_client.chat` 返回固定 `LLMResult` → `mode='llm'`，答案原文/来源/会话（含 label 新建生效、追问复用 conversation_id 且 label 不覆盖、消息累加 4 条、assistant sources_json JSON 往返一致）全部落库验证；同时断言传给 LLM 的提示词契约：system 为 design 冻结文案、user 含 `[来源: ...#]` 资料块与"问题：…"。
- **A5**: 列表项字段精确等于 `{id,label,created_at,message_count}`、按 id 降序、message_count 与实际消息数一致；详情 `{id,label,created_at,messages:[{id,role,content,sources,created_at}]}`；role 非法抛 ValueError、会话不存在抛 LookupError；sources_json 中文原样存储（ensure_ascii=False）；损坏 JSON 读路径防御为 `[]`；`ensure_conversation` 三分支（未指定/既有/不存在）语义正确。
- **补充 S1/S2/S3**: 接口签名与数据类字段冻结（供 T5 路由层）；一级成功路径（URL 拼接 `/chat/completions`、Bearer 头、answer strip、`LLM_TIMEOUT=7` 透传 requests）、一级空白回答判失败下落二级（探测 `/api/tags` + `/api/chat`、`OLLAMA_PROBE_TIMEOUT=3`/`OLLAMA_TIMEOUT=33` 透传）；空库无命中 → refusal 且不触达 LLM。

## 失败详情

无。

## 隔离与安全

- 每用例独立 SQLite 库 + 独立 Chroma 目录（monkeypatch `backend.db.DB_PATH` / `backend.rag.vectorstore.VECTOR_DIR` 并重置 `vectorstore._client` 单例，手法同 T3 测试）；模块导入前兜底重定向 `DB_PATH`/`VECTOR_DIR` 到 `.tmp_t4_tester/`。复跑后核验 `requirements/47846874/data/`（spd.db mtime 仍为 10-24 18:41）未被触碰。
- LLM 配置全部 monkeypatch 指向 127.0.0.1 本地端口（关闭端口=拒绝、黑洞服务=读超时），未发起任何真实外网请求。

## 遗留风险

1. **RAG_MIN_SCORE=0.30 阈值标定敏感（建议 PM/Coder 关注，非本次验收失败项）**：实测多个外域问题 top score 超过 0.30 而不会拒答——"推荐几部适合周末看的科幻电影" 0.3001（恰过线）、英文烘焙问题 0.3268、Kubernetes 问题 0.3774。bge-small-zh-v1.5 对中文闲聊/外域文本基线余弦相似度偏高，阈值 0.30 下"无关问题拒答"依赖问法措辞（本次所选无关问题 0.2555 稳定拒答，A3 判定有效）。是否上调阈值/加拒答前置规则属标定决策，交 PM/Coder 判断。
2. **ollama 二级"真实成功"路径未在本机实测**（本机无 ollama 服务）：二级逻辑（探测 URL、超时、响应解析、失败下落）已用假 requests 响应（S2）与黑洞/关闭端口（A1/A2）覆盖，真实 ollama 推理行为留待有环境时抽样验证。
3. **A2 耗时断言区间 [1.9, 6.0)s**：机器高负载下理论上可能偶发超上限，重跑即可；下限 1.9s 用于证明超时真实生效而非瞬时失败。
4. `_closed_port()`（bind 后释放取空闲端口）存在理论竞态，已用 `OLLAMA_PROBE_TIMEOUT=2` 兜底封顶失败耗时。

判定: 全部 5 条 acceptance 通过 → **PASS**
