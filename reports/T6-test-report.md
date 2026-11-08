# 测试报告 T6（前端静态页）

- 需求：47846874（医院 SPD 智能客服智能体 · Python RAG）
- 被测：`D:\ai\requirements\47846874\frontend\{index.html, knowledge.html, app.js, knowledge.js, style.css}`
- 判定依据：`D:\ai\requirements\47846874\design.md`『RESTful 契约』冻结表 +『前端』节
- 结果：**PASS**（22/22 用例通过，两轮复跑一致）

## 结果

PASS —— 5 条验收标准全部满足，无阻断、无失败。

```
22 passed in 3.73s / 复跑 22 passed in 3.95s
```

## 覆盖验收标准

| # | 验收标准 | 结论 | 佐证用例 |
|---|---|---|---|
| A1 | 仅用冻结契约路径与字段，无臆造接口 | ✓ | 6 条静态断言 + 2 条动态请求体断言 |
| A2 | JS 无语法错误（node --check）；HTML 闭合；DOM id 与 JS 引用一致 | ✓ | node --check ×2、html.parser 闭合检查、DOM id 三向交叉验证 |
| A3 | API 不可用时有可见错误提示，每个请求有 catch | ✓ | 静态 try/catch 全覆盖检查 + node DOM stub 真实执行兜底路径 |
| A4 | 无构建依赖（无 package.json/node_modules/CDN 外链） | ✓ | 目录清单 + 引用清单 + 框架特征扫描 |
| A5 | 无后端直开不抛未捕获异常阻塞渲染 | ✓ | DOM stub 运行 uncaught/unhandledRejection 均为空 + 静态服务 5 资源 200 |

## A1 契约对照清单（前端 7 调用点 ↔ design.md 冻结表）

| 调用点 | 方法 | 路径 | 请求体 | 消费的响应字段 | 与契约一致 |
|---|---|---|---|---|---|
| app.js checkHealth | GET | /api/health | - | status | ✓ |
| app.js loadConversations | GET | /api/conversations | - | id, label, message_count, created_at | ✓ |
| app.js openConversation | GET | /api/conversations/{id} | - | messages[.role, .content, .sources, .created_at] | ✓ |
| app.js sendMessage | POST | /api/chat | {question, conversation_id?} | conversation_id, answer, mode, sources[.doc_title, .seq, .score, .section_path, .snippet] | ✓ |
| knowledge.js loadDocuments | GET | /api/documents | - | id, title, filename, chunk_count, created_at | ✓ |
| knowledge.js onSubmitDoc | POST | /api/documents | {title, content} | id, chunk_count | ✓ |
| knowledge.js onDeleteDoc | DELETE | /api/documents/{id} | - | deleted, chunks_removed | ✓ |

- GET /api/documents/{id}/chunks 前端无调用点 —— 任务卡未要求，不计缺陷（与 Coder 移交说明一致）。
- 反向校验：剥离注释后两 JS 中所有 `/api` 字符串字面量仅落在冻结 6 个前缀内，无 `/api/v2`、`/api/chats` 之类臆造；无绝对 URL。
- 字段级校验：代码读取的全部属性名 ⊆ 契约响应字段 ∪ JS/DOM 内建白名单（白名单含 KeyboardEvent 的 key/keyCode/shiftKey/isComposing、Response 的 json/ok、classList/dataset 等，逐项可解释）；关键契约字段正面断言均被消费。

## A3/A5 动态验证方式（node DOM stub，非纯 grep）

夹具 `D:\ai\.tmp_t6_tester\dom_stub.js`：stub 最小 DOM（getElementById/createElement/classList/sessionStorage），`fetch` 一律 reject（模拟 T5 后端未启动），真实加载执行 app.js / knowledge.js 后触发 DOMContentLoaded 与交互事件，输出 JSON 断言：

- 无后端首开：error-banner 文案非空且 class 无 hidden；徽标显示「后端未连接」；uncaught/unhandledRejection/console.error 均为空 → 页面不阻塞渲染。
- 发送提问（send-chat）：真实发起 `POST /api/chat`，Content-Type=application/json，body 恰为 `{"question":"血糖试纸如何保存？"}`，失败后提示「提问失败：网络请求失败（后端未启动或地址不可达）：/api/chat」。
- 录入文档（submit-doc）：真实发起 `POST /api/documents`，body 恰为 `{title, content}`，失败后提示「录入失败：…」。
- 静态补充：所有发起请求的函数（checkHealth/loadConversations/openConversation/sendMessage/loadDocuments/onSubmitDoc/onDeleteDoc）函数体均含 try/catch；apiFetch 双层 try（网络异常 + JSON 解析异常）；两 JS 均无绕过 apiFetch 的裸 fetch。

## 其他核对

- node --check：app.js / knowledge.js 均通过（node v20.20.2）。
- HTML：html.parser 栈检查 index.html / knowledge.html 无未闭合、无交错闭合（input/meta/link 按 void 处理）。
- DOM id：app.js DOM_IDS(7) == index.html id(7)；knowledge.js DOM_IDS(7) == knowledge.html id(7)；`ui['…']` 引用 == DOM_IDS；运行时 getElementById 全命中（无缺失告警）。
- 构建依赖：frontend 目录仅 5 文件 + .gitkeep；HTML src/href 仅指向本地 5 资源；JS 无 require/import/export、无 React/Vue/webpack/vite/axios/jQuery 特征；style.css 无 @import/外链 url。
- 静态服务：`py -3 -m http.server 8123 --directory frontend` 下 5 资源全部 200 非空，测完进程已终止（8123 无 LISTENING 残留）。
- Coder 声明的两处补充已核：GET /api/health 状态徽标属冻结契约端点（合规）；新建会话用内存+sessionStorage、未新增后端端点（合规）。

## 需人工目测（无法自动化，待 T5 后由用户/PM 走查）

- 真实浏览器中的布局与交互观感（会话栏宽度、消息气泡样式、textarea 拉伸）。
- Enter 发送 / Shift+Enter 换行 / 中文输入法组词不误发（isComposing 逻辑已在源码确认，但真实输入法行为需人工）。
- window.confirm 删除确认弹窗的实际观感；DELETE 成功路径（依赖 T5 数据）。
- 双页互链跳转、样式加载后的视觉效果。

## 遗留风险

- `extract_functions` 依赖两 JS 现有的两空格缩进约定切分函数体；若后续代码风格改变，`test_a3_every_fetch_call_has_catch` 会以「未找到函数（提取器失效需修复测试）」显式失败提示修测试，不会静默漏检。
- 本轮 fetch 一律 reject，仅覆盖失败路径；成功路径的渲染（气泡/来源/徽标）属 A1 字段消费静态断言 + 人工目测范围，待 T5 联调。
- `tests/test_47846874_T6_static.py` 中 A1 契约字面量集合与 design.md 冻结表人工同步；契约若变更需同步更新该常量。

## 夹具与运行

- 测试：`D:\ai\tests\test_47846874_T6_static.py`
- 夹具：`D:\ai\.tmp_t6_tester\dom_stub.js`（node DOM stub）、`D:\ai\.tmp_t6_tester\probe_attrs.py`（白名单核定探针，非测试依赖）
- 运行：`cd D:\ai && export PYTHONIOENCODING=utf-8 && py -m pytest tests/test_47846874_T6_static.py -v`
- 手动复现兜底：`node D:\ai\.tmp_t6_tester\dom_stub.js D:\ai\requirements\47846874\frontend\app.js send-chat`
