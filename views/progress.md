# 进度与里程碑（PM 视角）

## 里程碑
| 里程碑 | 说明 | 状态 | 完成时间 |
|---|---|---|---|
| M1 | PRD 定稿 | 完成 | 2026-10-24 |
| M2 | 设计与任务拆解 | 完成 | 2026-10-24 |
| M3 | 编码完成 | 进行中 | |
| M4 | 测试全部通过 | 待开始 | |
| M5 | 收尾交付 | 待开始 | |

## 当前状态
- phase: executing（2026-10-25 续跑中，owner=harness-resume-<见会话>）
- 任务: 3/8 done（T1/T2/T6 done；T3 to_test——测试文件已由中断 Tester 写好待跑；**T8 新增**；T4/T5/T7/T8 pending）
- 波次: 本波=[T3 测试 + T8 编码] → [T8 测试 + T4 编码] → [T4 测试 + T5 编码] → [T5 测试 + T7 编码] → [T7 测试] → 收尾

### 2026-10-25 续跑记录
- 环境探活通过；**bge 模型缓存修复**：首次"预下载成功"是假象（管道 exit 0 来自 tail），实际 hf-xet 协议对 hf-mirror 401；以 HF_HUB_DISABLE_XET=1 + HF_ENDPOINT=hf-mirror.com 重下成功，缓存加载已验证（不带 xet 变量也 OK）→ **收尾 KB 候选：hf-mirror + hf-xet 401 坑**
- T8 新增（用户追加）：知识管理页 原始文件上传 + 分片管理界面，纯前端增强走冻结契约，无后端改动
- T3 Tester 首次派发被用户消息中断，遗留 tests/test_47846874_T3_vectorstore.py（328 行 8 测试，覆盖 S1/A1-A5/S2，未见报告）→ 重派时告知接手该文件

### 2026-10-25 会话记录（harness-resume-002633，中断存档）
- **环境重建**：仓库已迁至 E:\ai\ai，旧 Python 3.12 环境消失；PM 在 Python 3.10.0（D:\python\python.exe）按 design.md 约定命令重装全部依赖（chromadb 1.5.9 / sentence-transformers 6.2.0 / torch 2.14.1 / fastapi 0.162.0 等），bge-small-zh-v1.5 模型已缓存至 HF 本地缓存。**安装清单已归档：reports/env-install-2026-10-25.md + .log**，design.md『环境事实』已同步
- **T3 重派完成（编码）**：上一会话半成品（embedder/vectorstore 相对导入错误、bge 未接入检索链路）已由本轮 Coder 修复，ingest.py 新写；实现要点见 views/dev-notes.md T3 节。依赖装齐后未做运行级验证
- **T3 Tester 派发被取消**（用户有事务中断），无残留测试文件（tests/ 无 T3、reports/ 无 T3 报告）
- 锁：接管自死会话 harness-resume-000337 → 本会话结束前已 release

### 续跑指令（下一会话）
1. `/harness-resume 47846874`（或直接说"继续 47846874"）→ claim 锁
2. 探活环境：`py -3 -c "import chromadb, sentence_transformers, fastapi"`（缺失按 design.md 环境事实重装）
3. 派 T3 Tester：任务卡 acceptance + Coder 返回见 tasks.json T3 与 dev-notes.md T3 节；测试隔离注意 monkeypatch DB_PATH/VECTOR_DIR 与单例缓存（vectorstore._client / embedder._model）
4. T3 PASS → 依波次 W3=[T4] → W4=[T5] → W5=[T7]（每波 renew 锁）→ 全 done 后收尾五件事
- 风险 / 等待用户: 无（模型已缓存；T3 设计补充点见 dev-notes T3 节"与设计的偏离"，收尾时并入 design.md）

## 2026-10-25 晚（T9 追加段，最终态）
- 任务: **9/9 全部 done**（T1~T8 + T9 多轮对话上下文）；T9 经 1 轮 FAIL（拼接负优化）→ 强信号守卫+双查询 max 融合 PASS 13/13
- 里程碑 M3 ✓ M4 ✓ M5 ✓；phase=done
- 附加交付: DEPLOYMENT.md（环境清单+迁移备份手册）、CLAUDE.md 铁律 8（迁移/部署先备份）、KB 新增 hf-mirror-xet-401 / rag-multiturn-dual-query + subagent-429-resume 增补
- 注: 2026-10-25 16:30~17:50 另一并行会话完成加固轮（阈值 0.45/敏感闸/verify/多通道/文档编辑/双库加固），根 design.md 已整合；本会话与其无文件冲突
