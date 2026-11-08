# 需求 47846874 PRD

## 背景与目标
面向医院 SPD（供应 Supply / 加工 Processing / 配送 Distribution）场景的原型级智能客服智能体：基于 RAG，用预置的示例耗材知识库回答耗材目录、库存、申领流程、配送状态、计费结算、供应商资质等问题。
单机本地可运行（默认 Python 3.10+，与仓库根 design.md 约定一致），包含 SQLite 关系库持久化、本地向量存储、知识切片管道、RESTful 后端与网页前端。
目标是跑通"文档入库 → 切片 → 向量化 → 检索 → 生成回答 → 会话留痕"全链路并逐条验收，规模克制，不追求生产化。

## 功能范围

### 做什么
1. 示例语料与知识入库：预置 SPD 示例文档（建议 3~5 份，每份 ≥600 字，markdown/txt），提供入库入口（命令或 API）：文档加载 → 切片（每条切片带 doc_id、序号、原文文本等元数据）→ embedding → 写入向量库；文档与切片元数据同步落 SQLite。
2. 向量检索：按用户问题返回 top-k 相关切片及相似度得分，k 可配（默认 3~5）。
3. RAG 问答：将检索到的切片组装进提示词，由 LLM 生成答案，答案附来源引用（文档名 / 切片编号）；检索无命中时兜底拒答（明确提示"知识库中未找到"类文案，不编造 SPD 事实）；LLM 不可用时降级为"仅检索"模式，返回原文片段并标注未接入生成模型。
4. 会话留痕：会话（conversation）、消息（question/answer、来源）持久化到 SQLite，支持查询会话列表与会话内历史消息。
5. RESTful API：健康检查、问答、会话列表/详情、文档入库、文档列表、文档切片详情、文档删除（删除需同步清理向量库中对应向量）。
6. 前端页面（零构建链静态页，由后端托管）：聊天页（提问、显示答案与来源列表、新建/切换会话、查看历史消息）+ 知识管理页（文档列表、录入新文本文档、删除文档）。
7. 交付物：README（安装、启动、接口清单、配置说明）、pytest 自动化测试（覆盖切片、检索、问答 API 各至少 1 条用例）、建表 DDL 落 requirements/47846874/sql/。

### 明确不做什么（用户未要求，默认砍掉）
- 多租户、多院区隔离。
- 与真实 HIS / SPD / ERP / 医保系统对接，不抓取真实业务数据。
- 登录、RBAC 等用户权限体系（页面无鉴权，本机使用）。
- 生产级部署（Docker、HTTPS、域名、反向代理、环境分级）与高并发、压测、性能调优。
- 语音、图片等多模态输入；PDF/OCR 等复杂文档解析（语料仅 md/txt）。
- 模型微调（fine-tuning）、Agent 工具调用 / 自动执行业务操作（如代替科室下单）。
- 流式输出（SSE/打字机效果），问答一次返回完整 JSON；如用户后续要求再加。
- 多轮对话的上下文改写 / 指代消解（会话历史仅存档与展示，见待确认问题 Q6）。

## 验收标准
- A1 知识切片入库：对预置语料（≥3 份文档）执行入库后：(a) GET /api/documents 返回 ≥3 条文档记录；(b) 对每份文档调用切片详情接口返回 ≥2 条切片（每切片含 doc_id、序号、非空原文文本）；(c) SQLite 中 documents、chunks 表有对应持久化记录。语料每份 ≥600 字以保证可切出多片。
- A2 向量存储与检索：对 3 个预置主题问题（如含关键词"血糖试纸""领用流程""配送时效"）分别调用检索接口，均返回 top-k 切片且响应含相似度得分字段；排名第 1 的切片文本包含该问题的主题关键词（判定依据随语料提供的 qa_pairs 对照表）。
- A3 RAG 问答准确性：基于示例语料准备 ≥5 组"问题—答案关键词"对照表（由 Coder 随语料提供）。对每组提问调用问答接口，回答文本命中该组预设关键词 ≥1 个，且回答附带来源引用（文档名/切片编号）。若处于"未接入生成模型"降级模式，则按返回的检索片段命中关键词判定，并在回答中可见降级标注。
- A4 兜底拒答：提问与语料无关的问题（如"今天天气怎么样"），接口返回 HTTP 200 且回答包含"知识库中未找到"类兜底文案（具体文案待 Coder 确认），未编造 SPD 相关事实。
- A5 RESTful API 端点：以下端点逐一用 curl/页面验证状态码与关键字段（建议路径如下，最终以 Coder 设计为准并在 README 列明）：GET /api/health → 200；POST /api/chat（body：question，conversation_id 可选）→ 200，返回 answer 与 sources；GET /api/conversations → 200 会话列表；GET /api/conversations/{id} → 200 该会话全部消息；POST /api/documents（文本内容或文件）→ 200/201 并触发切片与向量化；GET /api/documents/{id}/chunks → 200；DELETE /api/documents/{id} → 200 且删除后检索不再命中该文档内容。
- A6 前端页面可用性：浏览器打开首页（建议 http://127.0.0.1:8000/，端口待 Coder 确认）：(a) 聊天页输入问题后 30 秒内显示答案与来源列表（降级模式下显示降级提示，流程不报错）；(b) 可新建会话并看到历史消息；(c) 知识管理页可查看文档列表，录入一份新文本文档后列表条数 +1。
- A7 数据库持久化：完成若干问答与文档入库后，停止并重新启动后端进程，GET /api/conversations/{id} 与 GET /api/documents 仍能查到此前全部数据；SQLite 数据文件与向量库持久化目录存在于项目目录内。
- A8 交付与可复现：按 README 在干净环境下用不超过 5 条命令完成安装并启动服务，GET /api/health 返回 200；pytest 全部测试通过。

## 涉及模块与接口
| 模块 | 建议路径 | 职责 | 边界 |
|---|---|---|---|
| 前端页面 | frontend/（原生 HTML+CSS+JS 静态文件，待 Coder 确认文件划分） | 聊天页 + 知识管理页，仅通过 REST API 与后端交互 | 不直接读写数据库/向量库，无构建链 |
| 后端 RESTful 服务 | backend/api/（建议 FastAPI，待 Coder 确认；亦可 Flask） | 路由与参数校验、编排 RAG 管道、托管前端静态文件、本地 CORS | 不含切片/embedding 业务逻辑本身 |
| RAG 管道 | backend/rag/ | 文档加载 → 切片 → embedding → 向量库写入/检索 → 提示词组装 → LLM 生成（可插拔客户端）→ 答案组装（引用+兜底+降级） | 不感知 HTTP 层，不直接操作前端 |
| 数据库（SQLite） | 单文件 DB + DDL 落 sql/ | 表：conversations、messages、documents、chunks（字段方向：会话/消息/文档/切片元数据，具体字段待 Coder 确认） | 向量本身不入关系库，chunks 表仅存元数据与原文 |
| 向量存储 | data/vectorstore/（建议 Chroma PersistentClient，待 Coder 确认） | 存切片向量与元数据（doc_id、序号），提供 top-k 相似检索 | 与 SQLite chunks 表按 ID 对应，文档删除时双向清理 |
| 示例语料 | data/docs/ | 3~5 份 SPD 文档（目录/申领/配送结算/供应商资质等）+ 1 份 qa_pairs 验收对照表 | 仅 md/txt |
| 配置 | .env / config（LLM、embedding 模型、k、端口等） | 切换在线 API / 本地 ollama / 降级模式 | 见待确认问题 Q1 |

RESTful 端点建议清单（最终路径待 Coder 确认，验收以 README 列明的为准）：GET /api/health；POST /api/chat；GET /api/conversations；GET /api/conversations/{id}；POST /api/documents；GET /api/documents；GET /api/documents/{id}/chunks；DELETE /api/documents/{id}；检索可用 POST /api/chat 的 sources 字段或独立检索端点承载。

## 待确认问题
- Q1 LLM 与 embedding 用什么？默认建议：embedding 用本地中文模型（如 sentence-transformers 的 BAAI/bge-small-zh-v1.5 或同量级），离线可跑、不依赖 key；生成模型采用三级降级——(1) .env 配置了 OpenAI 兼容 API（默认指向 DeepSeek，base_url/key/model 可配）则用在线；(2) 未配置但本机装有 ollama 则用 ollama 本地模型；(3) 两者都不可用则进入"仅检索"降级模式（返回原文片段并明确标注未接入生成模型）。这样无 key 也能完整验收。用户若有指定模型/已持 key，回复后按指定替换。
- Q2 向量库选型？默认建议：Chroma（本地持久化目录，pip 即装，自带元数据过滤与持久化，无需独立服务进程）；备选 FAISS（本地索引文件）。用户无偏好则按 Chroma 实施。
- Q3 关系库选型？默认建议：SQLite（Python 标准库 sqlite3，零部署，单文件持久化，满足 A7）；建表 DDL 落 requirements/47846874/sql/。不引入 MySQL/PostgreSQL 与迁移工具。
- Q4 前端形态？默认建议：原生 HTML+CSS+JS 静态页面，由后端 StaticFiles 托管，零构建链（不用 npm/webpack/Vue/React）；备选 Jinja2 服务端渲染。两者都不引入重型框架。
- Q5 知识文档从哪来？默认建议：由 Coder 编写 3~5 份 SPD 示例语料（耗材目录与说明、科室申领流程、配送与结算规则、供应商资质要求等主题）外加 1 份 qa_pairs 验收对照表，预置 data/docs/；不做真实数据抓取。用户若提供真实文档，入库流程不变，语料可直接替换。
- Q6 是否需要多轮对话理解（上下文改写/指代消解）？默认建议：本期不做，问答按单轮独立处理，会话历史仅存档与展示；若用户要求多轮，列为二期。
- Q7 页面是否需要登录/鉴权？默认建议：不做，本机原型直接使用；如需区分使用者，仅在前端让用户填写"科室/姓名"作为会话标签展示，不做服务端鉴权。
