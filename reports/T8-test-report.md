# 测试报告 T8

- 任务：T8 知识管理页增强：原始文件上传 + 分片管理界面（前端静态验收，后端 T5 未实现）
- 被测对象：`requirements/47846874/frontend/knowledge.html`（62 行）、`knowledge.js`（438 行）、`style.css`（286 行）
- 测试代码：`tests/test_47846874_T8_knowledge_ui.py`（30 用例）
- 复现命令（E:\ai\ai 下，Git Bash）：
  `export PYTHONIOENCODING=utf-8 && py -m pytest tests/test_47846874_T8_knowledge_ui.py -v`
- 结果：**30 passed in 5.02s**

## 结果: PASS

## 覆盖验收标准: A1 ✓ / A2 ✓ / A3 ✓ / A4 ✓ / A5 ✓（附加项 style.css 分片面板样式类 ✓）

### A1 文件上传（7 用例 ✓）
- 静态：`knowledge.html:26` 有 `<input id="doc-file-input" type="file" accept=".md,.txt">`；`knowledge.js:142-158` FileReader + `readAsText(file,'utf-8')`；`:145` 标题预填 `filenameStem(name)`（`:162-165` 按最后一个 `.` 截主干，无扩展名原样）；`:134` 有 `\.(md|txt)$/i` 扩展名兜底；`:152` 有 onerror 兜底；提交走 `POST /api/documents`（`:392-396`，body 恰为 `{title, content}`）。
- 动态（node DOM stub 真实执行 knowledge.js）：选择 `spd_耗材说明.md` → 标题=`spd_耗材说明`、正文=文件内容、读取提示可见；提交 → POST 体逐字节等于 `{"title":"spd_耗材说明","content":"# 使用说明\n\n正文第一段。"}`、JSON Content-Type、成功提示"已入库（文档 #9）"、表单清空、列表刷新（GET≥2 次）。选 `.pdf` → 报"不支持的文件类型"且 FileReader 零读取、清空选择；取消选择静默。

### A2 分片展开（6 用例 ✓）
- 静态：`:288` 按契约拼 `'/api/documents/' + encodeURIComponent(docId) + '/chunks'`；`renderChunksPanel/renderChunkItem` 消费的分片字段 `item.{seq,text,section_path,id}` ⊆ 契约五字段 `{id,doc_id,seq,text,section_path}`，无臆造字段；面板头 `'文档 #' + docId` 呈现文档 id（该端点返回分片的 doc_id 恒等于路径 id）；`renderDocTable` 消费字段恰等于 GET /api/documents 契约五字段；`:28/364-375` 阈值 200 字折叠（collapsed 类）+ 展开全文/收起全文切换。
- 动态：点"查看分片"→ 恰一次 GET `/api/documents/7/chunks` → chunk-row（colSpan=5）内 chunks-panel：头"文档 #7 共 2 个分片"，两 chunk-item 分别含 `seq 1`/`#71`/`使用说明 / 保存`/250 字长文（默认 collapsed，有展开按钮）与 `seq 2`/`#72`/无 path/短文（无展开按钮）；点"展开全文"collapsed 移除、按钮变"收起全文"；点"收起分片"行移除、按钮复原。分片接口挂 → 行内 `chunk-load-error`"分片加载失败…（可收起后重试）"、按钮恢复可用。

### A3 契约与无构建依赖（5 用例 ✓）
- knowledge.js 的 /api 字符串字面量恰为 `{"/api/documents","/api/documents/"}` ⊆ 冻结契约；apiFetch 调用点恰 4 处（GET/POST /api/documents、GET …/{id}/chunks、DELETE …/{id}）；POST 响应消费 ⊆ `{id,title,chunk_count}`、DELETE 响应消费 ⊆ `{deleted,chunks_removed}`；无臆造接口。
- frontend 目录恰 5 文件+.gitkeep，无 package.json/node_modules；HTML 仅本地引用（style.css/knowledge.js/index.html/knowledge.html），无 CDN/外链/绝对 URL；JS 无 import/require/框架特征；CSS 无 @import/外链 url()。

### A4 语法与 DOM 一致性（5 用例 ✓）
- `node --check` 过；HTML 标签闭合（python html.parser 栈校验）；DOM_IDS 9 项与 HTML 9 个 id、ui['…'] 实际引用三方双向相等；stub 运行无 console.error（无缺失 id）；script 置 </body> 前且初始化挂 DOMContentLoaded。

### A5 错误兜底与 T6 基线不回退（6 用例 ✓）
- 动态：fetch 全拒绝 → error-banner 非空文案"加载文档列表失败…"且去 hidden、无未捕获异常。
- T6 基线仍在：doc-form（标题/正文必填 + 提交按钮）submit 绑定→POST；手工提交失败路径真实发起 POST 且可见"录入失败"、按钮恢复；空表单本地校验"标题与正文均不能为空"且零请求；删除按钮 + `window.confirm`（文案含标题与切片数）——confirm 取消零 DELETE、确认后恰一次 `DELETE /api/documents/7` 且"已删除文档 #7，移除切片 2 个"、列表刷新。

### 附加项 style.css（1 用例 ✓）
被 JS 引用的分片面板/行为类全部有定义：chunks-panel、chunks-head、chunks-empty、chunk-list、chunk-item、chunk-meta、chunk-seq、chunk-id、chunk-path、chunk-text、btn-toggle-chunk、chunk-row、chunk-load-error、btn-secondary、file-hint、hidden、collapsed、doc-action、table-empty、btn-danger；且 `.chunk-text.collapsed` 规则含 `max-height`+`overflow: hidden`（真截断）。

## 失败详情: 无（首轮 3 例失败为测试 harness 自身缺陷——Elem 缺 click() 方法，修复测试工具后全绿；被测代码零修改）

## 遗留风险:
1. **分片 `doc_id` 未逐条消费**：`renderChunkItem`（knowledge.js:327-377）未读 `item.doc_id`，文档 id 由面板头"文档 #<docId>"（:308，取自行上下文而非响应字段）呈现。契约上该端点每个分片 doc_id 恒等于路径 id，用户可见信息无缺失，故不判 FAIL；若 PM 要求严格逐字段消费，可让面板头改用 `chunks[0].doc_id`（约 1 行改动）。
2. **`doc-row` 类无 CSS 定义**：knowledge.js:203 给文档行赋 `doc-row` 类，style.css 无对应规则。该类是惰性标记（JS 用 nextElementSibling 而非该类查询，也无样式诉求），无功能影响；可顺手删去或留作扩展钩子。
3. **文件读取固定 UTF-8**：`readAsText(file,'utf-8')` 对 GBK 编码的旧 .txt 会显示乱码（内容会原样入库）。单机原型可接受，如需兼容可在提示文案中说明或做编码探测。
4. 本卡为纯前端静态+stub 动态验收，未做真实 HTTP 联调；端到端（含 201/422/404 状态码路径）待 T5 完成后由集成验证补齐。

## 判定: PASS（acceptance 5 条 + style.css 附加项全部通过；遗留风险均不构成验收阻断）
