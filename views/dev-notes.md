# 开发详细设计日志（Coder 视角）

## T1 项目地基
- 实现要点: 骨架/config（路径推导+参数+HF 镜像）/db（sqlite3+DDL 初始化）/4 表 schema/requirements.txt/.env.example/空目录占位
- 改动文件: backend/{__init__,config,db}.py、backend/api/__init__.py、backend/rag/__init__.py、sql/schema.sql、requirements.txt、.env.example
- SQL: 4 表（conversations/messages/documents/chunks）+ 2 索引，全部 IF NOT EXISTS 幂等
- 与设计的偏离: 无
- 遗留问题: 无

## T2 SPD 示例语料与切片器
- 实现要点: chunker.py 纯函数切片（按 markdown 标题切逻辑段→段内递归字符切→<50 字碎片并入）；4 份语料 + _qa_pairs.md
- 改动文件: backend/rag/chunker.py、data/docs/spd_{catalog,requisition,delivery,vendor}.md、data/docs/_qa_pairs.md
- SQL: 无
- 与设计的偏离: 无
- 遗留问题: 无

## T6 前端静态页
- 实现要点: 零构建链原生 HTML/CSS/JS，聊天页 + 知识管理页，仅按契约路径/字段用 fetch 调 API
- 改动文件: frontend/index.html、frontend/knowledge.html、frontend/style.css、frontend/app.js、frontend/knowledge.js
- SQL: 无
- 与设计的偏离: 无
- 遗留问题: 无

## T3 向量存储与入库管道（重派：上一会话半成品 + PM 审查发现修复）
- 实现要点: embedder/vectorstore 修复相对导入(..config)；vectorstore 全链路接入 bge 嵌入(add_chunks→embeddings=、search→query_embeddings=)，冻结签名不变；ingest.py 新写：SQLite 单事务双写+提交前 Chroma 写入+补偿清理，delete 先 Chroma 后 SQLite 可重试收敛，filename 查重幂等更新，'_' 前缀拒绝
- 改动文件: backend/rag/{embedder,vectorstore,ingest}.py
- SQL: 无
- 与设计的偏离: 附加不破坏冻结契约——ingest_document 增可选 filename 参数(2 参调用兼容)、delete_document 返回切片数(对应 API chunks_removed)、新增 ingest_docs_dir()/filename_from_title；design.md 待补三点(见 coder design_md_updates)
- 遗留问题: 运行级验证留待 Tester（编码时依赖尚未装齐，仅 ast/语法与只读 import 冒烟）

## T3 修复轮（attempts=1）
- 缺陷: ingest.py:91 query_one(sql, (doc_filename)) 缺尾逗号 → sqlite3 ProgrammingError，入库链路全断
- 修复: (doc_filename,)；Coder 核验修复已在盘（落盘者不明：Tester 否认、修复 Coder 未编辑、疑似首个被取消 Tester 的未汇报动作；mtime 13:14:12 介于两 Tester 会话间）+ 自查 ingest.py 全部 8 处 SQL 绑定点无同类笔误
- 异常记录: ①两次 subagent 并行派发撞"model concurrency limit exceeded"，本会话并发=1 改串行 ②T8 首次派发报并发失败但代码实际已在后台写入完成（PM 已验：node --check 过、DOM_IDS 与 HTML 9 id 一致、功能齐备），T8 直接进 to_test

## T8 知识管理页增强（孤儿会话产出，PM 核验后进入测试）
- 实现要点: input file(.md/.txt)+FileReader 预填标题/正文→POST /api/documents；文档行"查看分片"→GET /api/documents/{id}/chunks 展开面板（seq/section_path/文本 200 字折叠可展开）；保留手工录入/删除确认；零构建链
- 改动文件: frontend/knowledge.html、knowledge.js、style.css
- 与设计的偏离: 无（纯前端走冻结契约，后端零改动）
- 遗留问题: 编码会话未正常回传（并发故障），验收全部交由 T8 Tester

## T4 LLM 客户端三级降级与 RAG 问答管道
- 实现要点: llm_client LLMResult(answer,mode)+chat(messages) 三级降级（OpenAI 兼容→ollama 探测/api/tags→retrieval_only），只捕具体异常；qa.answer(question,conversation_id=None,label=None) 检索→拒答(最高分<RAG_MIN_SCORE)→提示词→生成/降级→全 mode 落库，sources 严格契约六字段；chat_store 复用 db.py，列表 {id,label,created_at,message_count} 按 id 降序，sources_json ensure_ascii=False+损坏防御
- 改动文件: backend/rag/{llm_client,qa,chat_store}.py
- SQL: 无
- 与设计的偏离: ①qa.answer 比 design 签名多 label 参数（与契约一致，design 待补）②新增超时 env 覆盖 LLM_TIMEOUT/OLLAMA_PROBE_TIMEOUT/OLLAMA_TIMEOUT（默认 60s/2s/120s，design/.env.example 待记）③会话列表 id 降序约定
- 遗留问题: 无（mock 冒烟过，运行级验证待 Tester）

## T5 FastAPI 8 端点与静态托管
- 实现要点: create_app() 顺序装配 health→CORS→三组路由→启动装载语料(ingest_docs_dir 容错幂等)→最后 mount '/' 静态托管(FRONTEND_DIR 由 PROJECT_ROOT 推导)；POST /api/chat Field(min_length=1)+空白校验器双拦 422；documents 列表 LEFT JOIN 聚合 chunk_count；DELETE LookupError→404、ingest ValueError→422；模块级 app= create_app() 供 uvicorn backend.api.main:app，__main__ 起服 127.0.0.1:8000
- 改动文件: backend/api/{main,routes_chat,routes_conversations,routes_documents}.py
- SQL: 无（复用 schema.sql）
- 与设计的偏离: 无（启动装载语料为 design 意图的显式化）；另：四文件由一次未回传的并发故障会话先行写就，本次 Coder 逐条契约核验+TestClient 验证后确认无需改动
- 遗留问题: 无（运行级验收待 Tester 起真实服务）

## T7 README 与检索评估脚本
- 实现要点: eval_recall.py 解析 _qa_pairs.md（10 组）→ 三组参数（300/400/600，按线上关系推导 CHUNK_MIN/MAX/SECTION_MAX/OVERLAP、HARD_SPLIT_STEP 重算、上下文管理器退出还原）→ setattr chunker 常量后走 ingest_docs_dir 重建到 tempfile 临时库（SQLite/Chroma 双重定向+单例 reset+clear_system_cache）→ 逐题 search k=5 统计 recall@3/@5/MRR → markdown 对比表；README：简介（含 T8 上传/分片）+ ≤5 命令启动 + 契约表 8/8 核对一致 + 降级说明 + 配置/超时 + 已知限制 4 条 + 环境归档引用
- 改动文件: README.md、tools/eval_recall.py
- 与设计的偏离: 无；README 测试命令用 POSIX 路径 /e/ai/ai（Git Bash 规范）
- 遗留问题: ①脚本按卡未在本机运行（Tester 跑）②design.md『目录结构』尾注 D:\ai\tests 为迁移前旧路径，PM 收尾更正

## T9 多轮对话上下文（用户追加 2026-10-25 晚）
- 实现要点: chat_store.recent_turns(cid, turns=3) 按组回收（以 assistant 计轮、收满后带配对 user，冒烟修复漏半对缺陷）；qa.answer 开头（先于落库）取历史——检索查询=当前问题+最近 2 条历史用户问题（去重/单条 50 字/总量 300 字；无历史或全重复原样返回，首问逐字节一致）；提示词注入最近 3 轮区块（单条 200 字截断/区块 1200 字上限，头部声明"仅供理解追问指代、不作新事实来源"），系统提示仅在有历史时追加"结合历史对话理解用户的追问"；refusal/verify/retrieval_only 与契约零改动
- 改动文件: backend/rag/{qa,chat_store}.py
- 与设计的偏离: design.md『RAG 问答管道』节待补多轮描述（PM 收尾处理）
- 遗留问题: 运行级验收待 Tester

## T9 修复轮（attempts=1，双查询融合）
- 缺陷: 第 1 版 _augment_query 纯文本拼接负优化（强问 delivery top1 0.68→第 3；弱信号"那它送到科室要多久"跌出 top4）
- 修复: 强信号守卫（原问 top1≥0.45 直接用原问结果，仅 1 次检索，结构性杜绝负优化）+ 弱信号救援（原问低于阈值时用"当前问题+最近 1 条历史用户问题"二次检索）+ _fuse_hits 按 (doc_id,seq) 去重取较高分降序取前 k（池⊇原问池⇒逐位分数单调不降）；拼接条数 2→1（先行词局部性）；无历史路径与单轮版同一路径逐字节一致
- 改动文件: backend/rag/qa.py
- 遗留问题: 无
