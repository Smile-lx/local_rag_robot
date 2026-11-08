# 需求 47846874 交付总结（医院 SPD 智能客服智能体）

## 结果
**8/8 任务卡全部 done，全部验收 PASS**（2026-10-25）。单机 RAG 智能客服：Markdown 语料 → bge-small-zh 向量切片检索（Chroma + SQLite 双写双清）→ LLM 三级降级生成（OpenAI 兼容 API → ollama → 检索增强兜底）→ FastAPI 8 端点 + 零构建链双页前端（聊天页 + 知识管理页）。

## 交付物
| 层 | 内容 |
|---|---|
| RAG 管道 | backend/rag/：chunker（标题切分+递归字符切+重叠+碎片并入）、embedder（bge-small-zh-v1.5 单例）、vectorstore（Chroma 余弦）、ingest（双写双清/幂等更新/'_' 前缀跳过）、llm_client（三级降级+超时可覆盖）、qa（拒答阈值/来源引用/全模式落库）、chat_store |
| API | backend/api/：8 端点严格按冻结契约 + 静态托管 + 启动装载语料 |
| 前端 | frontend/：聊天页（会话栏/消息流/mode 标注/来源列表）、知识管理页（手工录入 + **.md/.txt 原始文件上传预填** + **分片查看展开面板** + 删除确认） |
| 工具与文档 | tools/eval_recall.py（3 组切片参数 × recall@3/@5/MRR）、README.md（≤5 命令启动/契约表/降级说明/已知限制/环境归档引用） |
| 语料 | data/docs/ 4 份 SPD 语料 + _qa_pairs.md（10 组评估对照，不入库） |

## 测试结论（reports/ 各卡报告 + views/test-summary.md）
- T1 地基 / T2 切片器 / T6 前端：PASS（2026-10-24 首轮波次）
- T3 检索入库：FAIL 1 轮（ingest.py:91 单元素元组缺尾逗号致 sqlite3 绑定错误）→ 修复后 PASS 9/9
- T4 问答管道：PASS 9/9（黑洞 TCP 实测超时降级；无关问题实测 score 0.2555 稳定拒答）
- T5 API 联调：PASS 8/8（真实 uvicorn 子进程 + HTTP 实调，泽塔合金 造数→命中→删除→不再命中）
- T8 知识页增强：PASS 30/30（静态契约 + node DOM stub 动态链路）
- T7 README/评估：PASS 4/4（干净环境按 README 启动成功；评估 recall@5=0.950、MRR=1.000；data/ 零污染）
- 全程正式库零污染（每卡测试前后快照核验）

## 本轮会话特殊事件（均已归档）
1. **环境重建**（2026-10-25）：仓库迁移 + Python 3.12 环境消失，在 Python 3.10.0 重装全部依赖、经 HF_HUB_DISABLE_XET=1 + hf-mirror 重下 bge 模型。安装清单归档：reports/env-install-2026-10-25.{md,log}
2. **追加需求 T8**（用户 2026-10-25）：知识管理页原始文件上传 + 分片管理界面，纯前端走冻结契约，后端零改动
3. **subagent 并发/容量故障频发**："model concurrency limit exceeded" 与 "Start Plan busy" 多次；实测**报错的派发可能已在后台跑完**（T5/T7 均为孤儿草稿+核验定稿）；应对：SendMessage 续跑既有 agent 绕过启动并发（沉淀入 KB patterns/subagent-429-resume.md）
4. **端口 8000 被 C-Lodop 打印服务占用**：README 已加 --port 8001 备注（沉淀入 KB pitfalls）

## 已知限制（README 同步）
- 无鉴权、单机原型；mode='llm' 需自配 LLM_API_KEY（无 key 时 retrieval_only 属预期）
- RAG_MIN_SCORE=0.30 偏松：外域问题实测 0.30~0.38 可能不拒答（T4 报告有数据，可 .env 上调）
- 文件上传固定 UTF-8（GBK 旧 txt 会乱码）；评估集 10 题偏易，区分度有限

## 补记（PM，2026-10-25 晚）
- 本 summary 记录的是本会话负责的 T1~T8 波次（至 14:55）。此后另一并行会话（16:30~17:50）追加了加固轮并完成验收，超 出 T1~T8 范围的能力以根 design.md 技术栈行为准：拒答阈值 0.30→0.45、敏感信息外发闸（SENSITIVE_EGRESS_BLOCK）、verify.py 防幻觉三重校验（verified 字段）、LLM_CHANNELS 多通道故障切换（/api/models）、GET /api/documents/{id} 文档原位编辑、上传 GBK 自动回退、分片面板头契约化、双库一致性加固（写互斥/busy_timeout/启动对账自愈）。KB 已沉淀 decisions/sensitive-egress-gate、pitfalls/port-8000-clodop-occupied；本会话另沉淀 pitfalls/hf-mirror-xet-401 与 patterns/subagent-429-resume 增补。

## T9 多轮对话上下文（2026-10-25 晚追加，已完成）
- 功能：每次 /api/chat 回顾该会话最近三轮对话——弱信号追问（"那它呢"）由单问拒答变为带上下文跨阈值作答（实测 top≈0.74）；强信号问题行为与单轮逐项一致（守卫仅 1 次检索），9 问句集验证融合逐位分数单调不降
- 质量：T3/T4/T5/T6/T7/T8/T9 回归全过（13 用例）；经历 1 轮 FAIL（首版纯拼接负优化）后改双查询 max 融合
- 变更：backend/rag/qa.py、chat_store.py（API 层零改动、契约不变）
- 另交付：DEPLOYMENT.md（设备环境清单+配置+迁移备份手册）、CLAUDE.md 铁律 8（迁移/部署前必先备份——用户要求的持久记忆）
