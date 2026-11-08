# 测试报告 T2（SPD 示例语料与切片器）

结果: **PASS**

- 测试文件: `D:\ai\tests\test_47846874_T2_chunker.py`（23 条用例）
- 运行命令: `cd /d/ai && export PYTHONIOENCODING=utf-8 && py -3 -m pytest tests/test_47846874_T2_chunker.py -v`
- 运行结果: 23 passed（Python 3.12.2 / pytest 9.1.1，0.2s；复跑通过，AIR 满足）
- 夹具目录: `D:\ai\.tmp_t2_tester\`（探针脚本 probe_chunker.py + samples/ 6 个构造样例，保留）

## 覆盖验收标准: 全部 ✓

| # | 验收标准 | 结论 | 关键证据 |
|---|---------|------|---------|
| A1 | 4 份语料每份 ≥600 字且含 ≥2 级标题结构 | ✓ | 全文/去空白字数: catalog 1394/1223, requisition 1090/974, delivery 1201/1086, vendor 1063/967；四份均含 1+2 级标题、二级标题 5~6 个 |
| A2 | _qa_pairs.md ≥5 组、覆盖三主题、关键词逐字命中 | ✓ | 程序化解析（跳空行/#注释，" | " split 一次，逗号+strip）得 10 组；每组关键词在其最佳匹配语料中 100% 逐字命中；三主题映射（血糖试纸→catalog、领用流程→requisition、配送时效→delivery）各有命中组 |
| A3 | 构造样例：seq/section_path、长度上限、相邻重叠、短段并入 | ✓ | seq 从 0 连续；三级路径「SPD 目录 / 检验类耗材 / 血糖试纸」精确匹配；全部块 ∈ [50,549]；同段相邻块重叠恰 70 字（b[:70]==a[-70:] 逐对断言）；首碎片并入下一段、末碎片兜底并入上一段；硬切步长 430 且去重叠后无损还原原文；split 签名无 filename |
| A4 | 4 份真实语料 ≥2 片/份且无空片 | ✓ | catalog 8 片、requisition 6 片、delivery 6 片、vendor 5 片（与 Coder 自验一致）；块长分布 [88,450]，无空片、无空路径；真实语料唯一同段相邻对（catalog seq1→seq2）重叠 70 ∈ [60,80] |
| A5 | 代码符合 conventions/python.md | ✓ | AST 校验：仅标准库（re/dataclasses/__future__）、无 import *、无裸 except、Chunk 为 @dataclass 且三字段全注解、公共函数参数+返回值注解完整、无 open(/print(（纯函数无 IO）、单行 ≤100 字符 |

## 边界与坏样例复核（A3 补充）

- `split("")` / 纯空白 → `[]` ✓
- 无标题长文 → section_path 全为 "" ✓
- `##无空格标题`、`#######` 七个 # → 不识别为标题，且原文不丢失 ✓
- 300 组固定种子随机文档 fuzz：块长 ∈ [50,549]、无空片、seq 连续，0 违规（探针脚本 `D:\ai\.tmp_t2_tester\probe_chunker.py`）

## 失败详情

无。

## 遗留风险

1. 口径 [50,549] 的下限只对非退化输入成立：整篇 <50 字的文档会返回单个短块（如 `split("短文本")` 得 1 片 3 字），测试未对这种退化场景施加下限断言（物理上不可能满足），已用例 `test_a3_edge_cases` 固化其行为。
2. A2 的"对应语料"采用程序化最佳匹配（关键词命中数最多的语料须全命中），未硬编码行→文件映射；当前 10 组的归属清晰（血糖试纸×2→catalog，申领×3→requisition，配送/退换/结算×3→delivery，供应商×2→vendor），若后续语料措辞改动导致关键词跨文件复现，覆盖判定仍稳健，但逐行归属需人工复核。
3. 真实语料中仅 catalog 产生 1 对同段相邻块，重叠 60~80 的校验样本量小；构造样例已用 2 对恰 70 字重叠补足，后续调整语料时该断言会自动扩展覆盖。
4. embedder/ingest（T3）尚未实现，chunk 元数据（doc_id、与 Chroma `doc_id:seq` 对应）不在本卡范围，未测。
