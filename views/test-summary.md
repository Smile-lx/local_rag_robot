# 测试汇总报告（Tester 视角）
> 单卡详情见 reports/test-report-<task_id>.md。

## 2026-10-24 wave1
- T1 foundation: PASS（reports/T1-test-report.md）
- T2 chunker: PASS（reports/T2-test-report.md）
- T6 static: PASS（reports/T6-test-report.md）
- 结论与遗留风险: T3-T5/T7 待测；embedding 模型下载耗时对测试耗时有影响

## 2026-10-25 wave2（T3 第 1 轮）
- T3 vectorstore/ingest: **FAIL**（reports/T3-test-report.md）——ingest.py:91 `query_one(sql, (doc_filename))` 缺尾逗号，sqlite3 ProgrammingError，A1/A2/A3/A4/S3 共 6 用例挂；A5/S1/S2 过。探针证实一行修复后预期全绿。attempts=1，已打回 Coder
- 结论与遗留风险: 缺陷为行级笔误非设计问题；测试文件 tests/test_47846874_T3_vectorstore.py 已增强至 9 用例（+S3 '_' 前缀/幂等扫描）
- T3 重跑: **PASS**（9/9，12.05s；A1~A5+S1/S2/S3 全绿，正式 data/ 零污染；第 1 轮 FAIL 保留为附录）→ T3 done

## 2026-10-25 wave3（T4 / T8）
- T4 qa/llm_client/chat_store: **PASS**（reports/T4-test-report.md，9 用例两轮 9 passed；A2 黑洞 TCP 实测超时受 env 约束 2.59s；A3 稳定拒答问句 top score 0.2555）
- 遗留风险（PM 决策）: RAG_MIN_SCORE=0.30 偏松——"推荐科幻电影"(0.3001)、英文外域(0.33-0.38)不拒答；维持 design 冻结值，进 README 已知限制与收尾汇报，T7 评估数据可支撑后续标定
- T8 knowledge UI: **PASS**（reports/T8-test-report.md，30/30；静态契约+node DOM stub 动态链路全验；首轮 3 败为测试 harness 自身缺陷，修工具后全绿，被测代码零改动）
- T8 非阻断遗留: doc_id 以面板头呈现（信息无缺失）、doc-row 类无 CSS 规则、GBK 旧 txt 读取乱码（固定 UTF-8）、真实 HTTP 联调待 T5 → T8 done（5/8）

## 2026-10-25 wave4（T5）
- T5 FastAPI 8 端点: **PASS**（reports/T5-test-report.md，真实 uvicorn 子进程 127.0.0.1:18174 + requests 实调，8 用例两轮全过；进程边界隔离 env 覆盖 DB_PATH/VECTOR_DIR/DOCS_DIR，正式 data/ 零污染；A3 泽塔合金 造数→命中→删除→不再命中 全流程）→ 7/8 done
- 遗留: mode='llm' HTTP 路径本机无 key 无法实测（qa 层 T4 已覆盖，路由仅透传，风险低）；RAG_MIN_SCORE 阈值敏感性同 T4

## 2026-10-25 wave5（T7，收尾前最后一卡）
- T7 README+eval: **PASS**（reports/T7-test-report.md，4 用例两轮 4 passed；A1 评估表 3 组×3 指标 recall@5=0.950/MRR=1.000/切片 25/25/24，data/ 全树快照零污染；A2 干净环境按 README 3 命令+8001 端口备注启动 health 200 且现场完整恢复；A3 接口清单逐 token 一致；A4 降级说明齐全）→ **8/8 全部 done**
- 遗留: 评估集 10 题偏易区分度有限（后续可加难）；8000 被打印服务占用已写入 README；RAG_MIN_SCORE 标定敏感性见 T4 报告

## 2026-10-25 晚（T9 第 1 轮）
- T9 多轮上下文: **FAIL**（reports/T9-test-report.md，11/12 过）——b/c/d/e 与 a 主断言全过；败因两条：①验收 a 对照组口径不当（"那它的配送时效呢"自带强关键词单问即 0.68 命中，PM 已修订口径为不变式）②真缺陷：_augment_query 文本拼接负优化——强问 delivery top1(0.68)→第3(0.59)、弱信号"那它送到科室要多久"拼接后跌出 top4。attempts=1 打回 Coder
- T9 修复轮复验: **PASS**（第 2 轮，13 用例两轮 13 passed；第 1 轮 FAIL 附录保留）——a-1 两组例句带历史与无历史逐项一致（守卫生效仅 1 次检索）+9 问句集融合逐位单调不降；a-2 真弱信号"那它呢"单问拒答→带历史 2 次检索跨阈值作答（top≈0.74）；b/c/d/e 沿用断言全过；正式库 md5 前后一致 → **9/9 全部 done**
