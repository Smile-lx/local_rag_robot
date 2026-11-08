# 测试报告 T7

结果: PASS

覆盖验收标准: A1 ✓ / A2 ✓ / A3 ✓ / A4 ✓

- 测试文件: `tests/test_47846874_T7_readme_eval.py`（4 个用例，对应 A1–A4；不 import backend，A1/A2 全走子进程，A3/A4 纯静态核对）
- 复现命令（Git Bash，工作目录 `E:\ai\ai`；A1 含模型加载+三组临时库重建，A2 含服务启动，全套约 30s~3min）:
  `export PYTHONIOENCODING=utf-8 && py -3 -m pytest tests/test_47846874_T7_readme_eval.py -v`
- 实测: 4 passed in 27.29s（复跑 4 passed in 27.64s，可重复）
- 环境: Windows 11 + Git Bash, Python 3.10.0, pytest 9.1.1；bge 模型已缓存本地

## 验收逐条结论

| 标准 | 用例 | 结论 |
|---|---|---|
| A1 eval_recall.py 跑通 + 3 组 × 3 指标表 + 绝不写正式库 | test_a1_eval_recall_three_groups_three_metrics_no_data_write | ✓ |
| A2 按 README 干净环境 ≤5 命令启动 + health 200 + 现场恢复 | test_a2_readme_clean_start_rebuild_and_health | ✓ |
| A3 README 接口清单 ↔ backend/api 四文件路由与响应字段 | test_a3_readme_route_table_matches_code | ✓ |
| A4 README 降级模式说明（无 key 属预期非故障） | test_a4_readme_documents_degradation_mode | ✓ |

## 关键实测数据

**A1（评估脚本）**：`py -3 tools/eval_recall.py`（cwd=requirements/47846874）退出码 0，10 题评估集、三组进度行齐全，输出 markdown 对比表（实测值）：

| chunk 目标 | CHUNK_MIN~CHUNK_MAX | OVERLAP | 切片总数 | recall@3 | recall@5 | MRR |
|---|---|---|---|---|---|---|
| 300 | 250~350 | 47 | 25 | 0.950 | 0.950 | 1.000 |
| 400 | 350~450 | 62 | 25 | 0.950 | 0.950 | 1.000 |
| 600 | 550~650 | 93 | 24 | 0.950 | 0.950 | 1.000 |

- 表结构断言：表头含 recall@3 / recall@5 / MRR / 切片总数；恰 3 数据行（300/400/600）、每行 8 列、3 指标均为 [0,1] 数值、切片总数 > 0、recall@5 ≥ recall@3（数学恒等，作指标计算正确性抽查）。
- **零污染证明**：跑前/跑后对 data/ 全树快照（文件路径 → 大小+mtime_ns，含 data/vectorstore 全部文件）**完全一致**——评估脚本对正式库零写入（临时库走 %TEMP%）。

**A2（README 干净环境启动）**：本机 8000 端口被 C-Lodop 打印服务（CLodopPrint32.exe, PID 14292）长期占用不可杀，**按 README 补丁备注改用 `--port 8001` 执行**（测试同时断言 README 含该备注，判定口径"README 可照做且成功"）：
1. 备份 data/spd.db 与 data/vectorstore/ 到 pytest 临时目录 → 删除二者（data/docs 语料 5 份不动）；
2. README 命令 1 照抄（pip install -r requirements.txt 清华镜像，依赖已装齐为 no-op，退出码 0）；命令 2（可选，cp .env.example .env，等价执行，测试后删除还原）；命令 3 按备注以 `--port 8001` 启动；
3. GET /api/health → 200 `{"status":"ok"}`（启动自动重建 data/spd.db 与 data/vectorstore）；
4. GET /api/documents → 装载语料文件集恰为 4 份 spd_*.md（`_qa_pairs.md` 以 "_" 开头不入库）；
5. finally：taskkill 杀干净 uvicorn、删重建产物、恢复备份；现场核验 data/ 文件集与大小复原、docs 5 份语料（大小+mtime）逐项未动、8001 端口释放、无残留进程、.env 已清理。全程 3 条命令（≤5）。

**A3（接口清单静态核对）**：解析 backend/api 四文件全部路由装饰器（含 main.py 的 /api/health 与三个 router × prefix），README 表格 8 行方法+路径与代码**集合相等**（路径参数名归一：README/design 用 `{id}`、代码用 `{doc_id}`/`{conversation_id}`，URL 形状等价后严格比对）；响应字段逐端点、逐 token 双侧对照（README 行 ↔ 实现模块源码：chat 响应四字段+sources 六嵌套字段、conversations 列表/详情、documents 列表/录入/chunks/删除等全部一致）；状态码族对照（POST documents 201 ↔ `status_code=201`；README "refusal 也是 200"/"参数缺失返回 422" ↔ 代码 404/422 HTTPException）。

**A4（降级说明）**：README 明确含 `mode="retrieval_only"`、"无 Key 时降级属预期行为，不是故障"、三级降级表（LLM_API_KEY 触发一级 / ollama 探测 /api/tags 二级 / 全不可用降级三级）及 LLM_TIMEOUT/OLLAMA_PROBE_TIMEOUT/OLLAMA_TIMEOUT 超时配置说明。

## 失败详情

无（最终判定以末次全套运行为准）。过程如实记录：首轮 2 项失败均为**测试自身问题**而非产品缺陷——①A2 中 PATH 的 `bash` 解析到 WSL bash 且无发行版导致字面 `cp` 失败，改为 shutil 等价执行；②A3 路径参数名 `{doc_id}` vs `{id}` 字面不等，加参数名归一化（URL 形状仍严格比对）；③A2 现场校验把 data/docs 的 `.gitkeep` 占位文件误算进全等集合，改为语料 5 份子集断言（docs 前后全等校验保留）。三处修正均未弱化断言强度（快照零变化、契约字段逐 token、health 精确体等核心断言原样）。

## 遗留风险

1. **8000 端口长期被用户打印服务占用**：README 已注明 8001 备选（本次验证通过），但本机默认 8000 的开箱体验不可用；属环境事实，文档已覆盖，无代码行动项。
2. **A2 的 pip 步骤在本机是 no-op 验证**（依赖已装齐，"Requirement already satisfied"）：未验证全新机器从零安装路径，该场景由 `reports/env-install-2026-10-25.md` 归档覆盖。
3. **评估集偏易**：10 题三组指标接近满分（recall 0.950 / MRR 1.000），对 chunk 参数选择的区分度有限；线上默认 CHUNK_TARGET=450 落在 400 组附近。建议 PM 后续扩充/加难评估集以提升参数对比的判别力（不阻塞本卡）。
4. README 命令 2（cp .env）为可选步骤，本机以等价复制执行（WSL bash 不可用属本机 shell 环境特性，非 README 缺陷）。

判定: 全部 4 条 acceptance 通过 → **PASS**
