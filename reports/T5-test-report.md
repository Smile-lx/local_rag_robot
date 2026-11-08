# 测试报告 T5

结果: PASS

覆盖验收标准: A1 ✓ / A2 ✓ / A3 ✓ / A4 ✓ / A5 ✓

- 测试文件: `tests/test_47846874_T5_api.py`（8 个用例：A1–A5 验收 + S1 CORS / S2 清库拒答补充）
- 复现命令（Git Bash，工作目录 `E:\ai\ai`，服务启动含模型加载约 20s）:
  `export PYTHONIOENCODING=utf-8 && py -3 -m pytest tests/test_47846874_T5_api.py -v`
- 实测: 8 passed in 20.35s（二次复跑 8 passed in 19.97s，可重复）
- 环境: Windows 11 + Git Bash, Python 3.10.0, pytest 9.1.1；uvicorn 子进程监听 127.0.0.1:18174，HTTP 实调（requests）
- 被测对象: `backend/api/main.py`、`routes_chat.py`、`routes_documents.py`、`routes_conversations.py` + `frontend/` 静态托管

## 测试方式与隔离

- **真实 uvicorn 子进程**：`py -3 -m uvicorn backend.api.main:app --port 18174`（cwd=requirements/47846874），轮询 `/api/health` 就绪（超时 240s）；teardown `taskkill /PID /T /F`，复跑后 `tasklist` 确认无残留进程。
- **进程边界隔离（env 注入，monkeypatch 对子进程无效）**：`DB_PATH`/`VECTOR_DIR`/`DOCS_DIR` 指向 pytest 临时目录（config 在子进程 import 时读取 env，已先读 `backend/config.py` 确认 `_env_path` 机制）；`FRONTEND_DIR` 保持正式目录（A4 需真实页面）。复跑后核验 `requirements/47846874/data/`（spd.db mtime 10-24 18:41、docs/ 5 份语料 mtime 10-24）完全未动。
- **确定性 LLM 配置**：子进程 `LLM_API_KEY=""` + `OLLAMA_BASE` 指向本地关闭端口 → chat 全部确定性降级 `mode='retrieval_only'`（无 key 属契约预期行为，PM 已确认不算失败）；全程无真实外网请求。
- 种子文档放临时 `DOCS_DIR`（同时验证启动装载语料路径 `ingest_docs_dir` 工作），其余造数走 `POST /api/documents`。

## 验收逐条结论

| 标准 | 用例 | 结论 |
|---|---|---|
| A1 GET /api/health 200 {"status":"ok"} | test_a1_health_ok | ✓ 响应体精确相等 |
| A2 契约表 8 端点状态码与字段名完全一致 | test_a2_chat_and_conversations_contract、test_a2_documents_contract | ✓ |
| A3 DELETE 后 chat 不再命中该文档 | test_a3_delete_document_then_chat_no_hit | ✓ |
| A4 GET / 聊天页 HTML、style.css/app.js 200 | test_a4_static_frontend_served | ✓ |
| A5 question 缺失/空/纯空白 → 422 | test_a5_question_validation_422 | ✓ |

## 关键实测数据（A2 逐端点）

- **POST /api/chat**：200，响应字段精确 `{conversation_id,answer,mode,sources}`；sources 项字段精确 `{doc_id,doc_title,seq,section_path,score,snippet}`；retrieval_only 答案含检索片段；**refusal 也 200**（无关问题 mode='refusal'、sources=[]、同字段集）。
- **GET /api/conversations**：`[{id,label,created_at,message_count}]` 字段精确、id 降序、两轮问答后 message_count=4、label 生效且追问不覆盖。
- **GET /api/conversations/{id}**：`{id,label,created_at,messages:[{id,role,content,sources,created_at}]}` 字段精确，消息序列 user/assistant 交替，assistant sources 与 chat 响应逐项一致；`/999999` → 404 `{"detail":...}`。
- **GET /api/documents**：`[{id,title,filename,chunk_count,created_at}]` 字段精确；种子文档在列（证明启动装载语料路径工作）。
- **POST /api/documents**：201，`{id,title,chunk_count}` 字段精确；空白 title → 422；缺 content → 422。
- **GET /api/documents/{id}/chunks**：`[{id,doc_id,seq,text,section_path}]` 字段精确、seq 升序、条数与录入返回 chunk_count 一致；不存在文档 → 404 detail。
- **DELETE /api/documents/{id}**：`{deleted:true,chunks_removed:int}` 字段精确且 chunks_removed 与录入数一致；重复删除 → 404 detail。
- 补充 S1：带 Origin 头请求 `access-control-allow-origin: *`（CORS 生效）；补充 S2：清空全部文档后 chat → 无命中 refusal 200（确定性拒答路径）。

## A3 流程实测

独有主题文档（"泽塔合金耗材管理细则"）POST → 201；chat"泽塔合金耗材的库存保管要求是什么" → mode=retrieval_only 命中，sources 含新文档 doc_id、答案含独有关键词"泽塔合金"；DELETE → 200；同问再 chat → sources 无该 doc_id 且答案不含"泽塔合金"（不再命中该文档内容）。

## 失败详情

无。

## 遗留风险

1. **mode='llm' 的 HTTP 路径未在本机实测**（无 LLM_API_KEY、无 ollama 服务）：本卡实测了 retrieval_only 与 refusal 两种 mode 的契约字段；llm 模式的字段组装在 qa 层已由 T4 测试（monkeypatch 固定回答）覆盖，routes_chat 仅透传，风险低。接入真实 key 后建议抽样一次 mode='llm' 的端到端联调。
2. **无关问题拒答的阈值敏感性（同 T4 报告遗留）**：A2 拒答检查在"仅种子文档"语料下实测 top score ≈0.19 稳定拒答；多文档语料下外域问题可能超过 RAG_MIN_SCORE=0.30 不拒答（T4 实测 0.30–0.38），上线表现依赖语料规模与阈值标定，建议 PM 结合 T7 评估结论决策。
3. 固定端口 18174：启动前有 bind 预检，被占用时报错明确；但两份本测试不可并行同跑（端口冲突）。
4. 用例共享同一服务实例（session 级 fixture），存在状态顺序（拒答检查在造数前、清库拒答在最后、A3 在契约测试后），依赖 pytest 文件顺序执行（默认行为满足）；若引入随机排序插件需加 ordering 约束。
5. 启动装载语料以临时目录 1 份种子文档验证装载路径工作；正式 `data/docs/` 4 份语料的装载与检索质量属 T7 评估范畴，本卡未复测。

判定: 全部 5 条 acceptance 通过 → **PASS**
