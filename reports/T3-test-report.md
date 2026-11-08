# 测试报告 T3（向量存储与入库管道）

结果: **PASS**（修复后重跑；2026-10-25 13:18:35，9/9 用例通过，12.05s）
覆盖验收标准: A1 ✓ / A2 ✓ / A3 ✓ / A4 ✓ / A5 ✓（5/5 通过）
补充检查: S1 接口冻结 ✓ / S2 归一化与维度 ✓ / S3 '_' 约定与目录扫描 ✓（3/3 通过）

- 被测对象: `requirements/47846874/backend/rag/{embedder,ingest,vectorstore}.py`（Coder 交付 + ingest.py:91 一处修复，Tester 未改动任何 backend/ 源码）
- 测试环境: Windows 11 + Git Bash，`py` = Python 3.10.0；chromadb 1.5.9 / sentence-transformers 6.2.0 / torch 2.14.1+cpu / pytest 9.1.1 / numpy 2.1.2；bge-small-zh-v1.5 模型走本机缓存（未触发下载，无需 HF_HUB_DISABLE_XET）
- 测试文件: `E:\ai\ai\tests\test_47846874_T3_vectorstore.py`（9 用例）
- 运行命令: `cd /e/ai/ai && export PYTHONIOENCODING=utf-8 && py -m pytest tests/test_47846874_T3_vectorstore.py -v`
- 重跑日志: `E:\ai\ai\.tmp_t3_tester\pytest-rerun.log`；隔离快照 `.tmp_t3_tester/data_before_rerun.txt` / `data_after_rerun.txt`（diff 为空，正式 `data/` 目录零污染）
- 时间线: 第 1 轮 pytest 13:11:59 FAIL → 诊断探针 13:12:13 → ingest.py 修复落盘 13:14:12（Coder 侧应用，非 Tester）→ 修复后重跑 13:18:35 全绿

## 逐条验收证据（修复后重跑，全部通过）

### A1 入库一致 ✓
`ingest_document` 对三小节样例返回 `(doc_id>0, chunk_count=3)`：documents 表 1 行（title/filename/ content 与输入一致，缺省 filename 由 `filename_from_title` 推导）；chunks 表行数 == 返回切片数、seq 从 0 连续、text/section_path 非空；`vectorstore.count()` == chunk_count。

### A2 检索命中 ✓
空库（集合不存在）检索返回 `[]` 不报错；入库样例（3 片）+ 无关干扰文档（2 片，行政会议主题）后，`search(自句查询, k=4)` 返回 Hit 列表：score 均为 float 且 ∈[-1,1]、降序排列，doc_id/doc_title/section_path 溯源字段齐全；top1 命中样例文档、切片含被查询自句、score>0.5，且严格高于任何干扰文档命中。

### A3 删除双清 ✓
`delete_document` 返回删除切片数与实际一致；documents/chunks 两表无残留；Chroma count 恰减少 chunk_count；自句检索不再命中已删文档；再次删除抛 LookupError（API 404 依据）。

### A4 幂等更新 ✓（两条路径）
显式 filename 重复 ingest：doc_id 不变、documents.content 更新为新版本、恰 1 片且旧切片整体替换（seq 连续无残留）、documents 不产生重复行、Chroma count == 新切片数（旧向量已清）；缺省 filename（同标题两次）派生同一去重键，同样幂等。

### A5 下载失败指引 ✓
monkeypatch 破坏模型名触发下载失败：抛 RuntimeError，消息含 `HF_ENDPOINT`、`hf-mirror` 镜像指引与坏模型名 `BAAI/t3-nonexistent-model-probe`；`__cause__` 保留原始异常链；失败后 `_model` 仍为 None（不缓存半初始化单例）。

### 补充检查 S1/S2/S3 ✓
S1：Hit 字段集 `[doc_id, seq, text, score, doc_title, section_path]`、7 个公共函数签名与 design.md『接口冻结』一致，`search` k 默认 4、`ingest_document` filename 默认 None。S2：embed 输出 shape=(n,512) 且 L2 范数≈1，空列表返回 0 行。S3：`_` 前缀 filename 拒绝（ValueError）且 SQLite/Chroma 零残留；`ingest_docs_dir` 跳过 `_` 文件、入库后两侧计数一致、重复扫描幂等不新增文档。

## 遗留风险

1. ingest.py:91 的缺陷曾使全部入库链路不可用，本轮已验证修复有效；该修复仅 Coder 自查 + 本测试回归覆盖，无其他已知风险点。
2. 模型走本机缓存加载，"首次下载成功"路径未在测试内覆盖；下载**失败**指引已由 A5 覆盖（两轮运行均未出现 hf-xet 401，未启用 HF_HUB_DISABLE_XET）。
3. Chroma `PersistentClient` 为进程级单例且持有 Windows 句柄，测试夹具按用例重置 `vectorstore._client`；若后续 Coder 改为 import 期创建 client，本测试的隔离方式需要同步调整。
4. A2 语义质量断言（top1 > 0.5 且高于干扰文档）基于当前语料形态，若 T5 评估阶段发现召回不足，属调参范畴而非接口缺陷。

---

# 附录：第 1 轮 FAIL 记录（2026-10-25 13:11:59，已被修复后重跑取代）

结果: FAIL（A1 ✗ / A2 ✗ / A3 ✗ / A4 ✗ / A5 ✓；补充 S1 ✓ / S2 ✓ / S3 ✗）——6 个用例卡在同一处一行级缺陷。

**缺陷位置: `requirements/47846874/backend/rag/ingest.py:91`**

```python
existing = query_one("SELECT id FROM documents WHERE filename = ?", (doc_filename))   # 缺尾逗号
```

- 期望 vs 实际: `(doc_filename)` 是"括号分组"而非单元素元组。`sqlite3` 把 str 按字符序列展开绑定：语句 1 个占位符、实收 `len(filename)` 个参数 → 任何长度 ≠ 1 的 filename 100% 抛 `sqlite3.ProgrammingError: Incorrect number of bindings supplied. The current statement uses 1, and there are N supplied.`（N = filename 字符数，如 `血糖试纸效期管理规范.md` → 13、`dup_reingest.md` → 15、`regular.md` → 10）。
- 影响面: `ingest_document` 全部正常入口——显式多字符 filename、缺省 filename（标题推导）、`ingest_docs_dir` 扫描的正常文件，全在查重一步抛错，致 A1/A2/A3/A4×2/S3 共 6 用例同点失败。
- 修复: 第 91 行改为 `(doc_filename,)`（Coder 已应用，2026-10-25 13:14:12 落盘；Tester 未改动 backend/ 源码）。ingest.py 其余 7 处 SQL 绑定点（L100/102/107/112/142/150/152）两轮均核验正确。
- 第 1 轮诊断探针（1 字符 filename 绕过该行，`.tmp_t3_tester/probe-run.log`）当时已证明管线其余环节无缺陷：计数一致、自句检索 top1 score=0.9005、同名幂等更新、删除双清/LookupError 全部 OK——与修复后重跑结论互相印证。
- 第 1 轮日志: `.tmp_t3_tester/pytest-run.log`；隔离快照 `.tmp_t3_tester_data_before.txt` / `_after.txt`（同为零污染）。

## 测试文件说明（Tester 维护）

继承上一中断派发的 8 用例经逐条复核**如实覆盖 acceptance、无弱化**（A2 语料前提"样例 3 片 + 干扰 2 片 = 5 片，k=4 必含干扰"已用 chunker 实测确认），原断言未改动。本轮接管新增三处增强：S1 默认值冻结（k=4 / filename=None）、A4 "documents.content 确被 UPDATE 为新版本"断言、新增 S3（`_` 前缀拒绝零残留 + `ingest_docs_dir` 跳过 `_` 文件且重复扫描幂等）。

## 复现

```bash
cd /e/ai/ai && export PYTHONIOENCODING=utf-8 && py -m pytest tests/test_47846874_T3_vectorstore.py -v
```
