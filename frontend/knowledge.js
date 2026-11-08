/* knowledge.js —— 知识管理页逻辑（原生 ES6 + fetch；无框架、无构建、无 CDN 外链）
 * 接口契约：requirements/47846874/design.md『RESTful 契约』（冻结，不臆造接口）
 * 本页调用端点：
 *   GET    /api/documents                → 文档列表（id/title/filename/chunk_count/created_at）
 *   POST   /api/documents                → 录入文档（{title, content}，201 返回 {id,title,chunk_count}）
 *                                          手工录入与文件上传（本地读文本预填表单后）共用此端点
 *   GET    /api/documents/{id}/chunks    → 分片列表（[{id,doc_id,seq,text,section_path}]）
 *   DELETE /api/documents/{id}           → 删除文档（confirm 确认后调用，返回 {deleted,chunks_removed}）
 */
(function () {
  'use strict';

  const ui = {}; // id -> DOM 节点；id 列表见 DOM_IDS，必须与 knowledge.html 中定义一致

  const DOM_IDS = [
    'error-banner',
    'success-banner',
    'doc-form',
    'doc-file-input',
    'file-load-hint',
    'doc-title-input',
    'doc-content-textarea',
    'submit-doc-btn',
    'doc-table-body',
  ];

  // 分片文本超过该字符数时默认折叠（截断显示），提供“展开全文/收起全文”切换
  const CHUNK_PREVIEW_CHARS = 200;

  // 文档表格列数：ID/标题/切片数/创建时间/操作（分片展开行跨全表）
  const DOC_TABLE_COLSPAN = 5;

  document.addEventListener('DOMContentLoaded', init);

  function init() {
    for (const id of DOM_IDS) {
      ui[id] = document.getElementById(id);
    }
    const missing = DOM_IDS.filter((id) => !ui[id]);
    if (missing.length > 0) {
      console.error('[knowledge.js] knowledge.html 缺少以下 DOM id：' + missing.join(', '));
      return; // 不绑定事件，避免对空节点操作抛未捕获异常
    }
    ui['doc-form'].addEventListener('submit', onSubmitDoc);
    ui['doc-file-input'].addEventListener('change', onFileSelected);
    loadDocuments();
  }

  /* ---------------- fetch 封装与页面提示 ---------------- */

  async function apiFetch(path, options) {
    const opts = options || {};
    let res;
    try {
      res = await fetch(path, opts);
    } catch (err) {
      throw new Error('网络请求失败（后端未启动或地址不可达）：' + path);
    }

    let body = null;
    try {
      body = await res.json();
    } catch (err) {
      if (res.ok) {
        throw new Error('响应不是合法 JSON：' + path);
      }
      // 非 2xx 且响应体不是 JSON：走下方按状态码抛错
    }

    if (!res.ok) {
      const detail = detailFromBody(body);
      throw new Error('HTTP ' + res.status + (detail ? '：' + detail : '') + '（' + path + '）');
    }
    return body;
  }

  // 兼容 FastAPI 422 的 detail 数组形态
  function detailFromBody(body) {
    if (!body || body.detail === undefined || body.detail === null) return '';
    const d = body.detail;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) {
      return d.map((item) => (item && item.msg ? String(item.msg) : JSON.stringify(item))).join('; ');
    }
    try {
      return JSON.stringify(d);
    } catch (err) {
      return String(d);
    }
  }

  function errMessage(err) {
    return err && err.message ? String(err.message) : String(err);
  }

  function showError(message) {
    ui['success-banner'].classList.add('hidden');
    ui['error-banner'].textContent = message;
    ui['error-banner'].classList.remove('hidden');
  }

  function showSuccess(message) {
    ui['error-banner'].classList.add('hidden');
    ui['success-banner'].textContent = message;
    ui['success-banner'].classList.remove('hidden');
  }

  function clearError() {
    ui['error-banner'].textContent = '';
    ui['error-banner'].classList.add('hidden');
  }

  function formatTime(value) {
    if (value === null || value === undefined || value === '') return '—';
    const d = new Date(value);
    if (isNaN(d.getTime())) return String(value); // 非标准时间格式则原样展示
    const pad = (n) => String(n).padStart(2, '0');
    return (
      d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes())
    );
  }

  /* ---------------- 原始文件上传（FileReader 读文本 → 预填表单，提交仍走 POST /api/documents） ---------------- */

  function onFileSelected(e) {
    const input = e.target;
    const file = input.files && input.files[0];
    if (!file) {
      setFileHint(''); // 取消选择
      return;
    }
    const name = String(file.name || '');
    if (!/\.(md|txt)$/i.test(name)) {
      // accept 已限制选择框，但拖拽/“所有文件”仍可能绕过，这里兜底校验
      showError('不支持的文件类型「' + name + '」：仅支持 .md / .txt 文本文件，请重新选择。');
      input.value = '';
      setFileHint('');
      return;
    }

    const reader = new FileReader();
    reader.onload = () => {
      const content = typeof reader.result === 'string' ? reader.result : '';
      if (content.indexOf('�') !== -1) {
        // UTF-8 解码出现替换符：大概率是 GBK 等本地编码旧文档，回退按 GBK 重读
        const gbkReader = new FileReader();
        gbkReader.onload = () => {
          fillFromFile(
            name,
            typeof gbkReader.result === 'string' ? gbkReader.result : '',
            'GBK'
          );
        };
        gbkReader.onerror = () => showReadError(name, input);
        gbkReader.readAsText(file, 'gbk');
        return;
      }
      fillFromFile(name, content, 'UTF-8');
    };
    reader.onerror = () => showReadError(name, input);
    reader.readAsText(file, 'utf-8');
  }

  // 读文件成功后预填表单（encoding 为实际识别编码；上传新文件即解锁标题）
  function fillFromFile(name, content, encoding) {
    ui['doc-title-input'].disabled = false;
    ui['doc-title-input'].value = filenameStem(name);
    ui['doc-content-textarea'].value = content;
    clearError();
    setFileHint(
      '已读取「' + name + '」（' + content.length + ' 字符，识别为 ' + encoding +
      ' 编码），标题与正文已填充，可修改后点“提交入库”。'
    );
  }

  // 读文件失败提示（首次读取与 GBK 回退共用）
  function showReadError(name, input) {
    showError('读取文件「' + name + '」失败，可改用下方正文框手工粘贴录入。');
    input.value = '';
    setFileHint('');
  }

  // 文件名主干：去掉最后一个扩展名（“spd_耗材.md”→“spd_耗材”；无扩展名原样返回）
  function filenameStem(name) {
    const dot = name.lastIndexOf('.');
    return dot > 0 ? name.slice(0, dot) : name;
  }

  function setFileHint(message) {
    const el = ui['file-load-hint'];
    if (!message) {
      el.textContent = '';
      el.classList.add('hidden');
    } else {
      el.textContent = message;
      el.classList.remove('hidden');
    }
  }

  /* ---------------- 文档列表（GET /api/documents） ---------------- */

  async function loadDocuments() {
    try {
      const list = await apiFetch('/api/documents');
      clearError();
      renderDocTable(Array.isArray(list) ? list : []);
    } catch (err) {
      renderDocTable([]); // 渲染为空表提示，避免残留"加载中…"
      showError('加载文档列表失败：' + errMessage(err));
    }
  }

  function renderDocTable(list) {
    const tbody = ui['doc-table-body'];
    tbody.textContent = '';
    if (!Array.isArray(list) || list.length === 0) {
      renderDocTableEmpty();
      return;
    }
    for (const doc of list) {
      const item = doc || {};
      const docId = item.id;
      const hasId = docId !== undefined && docId !== null;
      const tr = document.createElement('tr');
      tr.className = 'doc-row';

      const tdId = document.createElement('td');
      tdId.textContent = hasId ? String(docId) : '—';
      tr.appendChild(tdId);

      const tdTitle = document.createElement('td');
      tdTitle.textContent =
        item.title === null || item.title === undefined ? '（无标题）' : String(item.title);
      if (item.filename !== null && item.filename !== undefined && String(item.filename) !== '') {
        tdTitle.title = String(item.filename); // 悬停可看实际文件名（契约字段，非必需展示）
      }
      tr.appendChild(tdTitle);

      const tdCount = document.createElement('td');
      tdCount.textContent = typeof item.chunk_count === 'number' ? String(item.chunk_count) : '—';
      tr.appendChild(tdCount);

      const tdTime = document.createElement('td');
      tdTime.textContent = formatTime(item.created_at);
      tr.appendChild(tdTime);

      const tdAction = document.createElement('td');
      tdAction.className = 'doc-action';

      const btnEdit = document.createElement('button');
      btnEdit.type = 'button';
      btnEdit.className = 'btn-secondary';
      btnEdit.textContent = '编辑';
      if (!hasId) {
        btnEdit.disabled = true; // 缺 id 的脏数据无法编辑
      } else {
        btnEdit.addEventListener('click', () => onEditDoc(docId));
      }
      tdAction.appendChild(btnEdit);

      const btnChunks = document.createElement('button');
      btnChunks.type = 'button';
      btnChunks.className = 'btn-secondary';
      btnChunks.textContent = '查看分片';
      if (!hasId) {
        btnChunks.disabled = true; // 缺 id 的脏数据无法查分片
      } else {
        btnChunks.addEventListener('click', () => onToggleChunks(tr, docId, btnChunks));
      }
      tdAction.appendChild(btnChunks);

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn-danger';
      btn.textContent = '删除';
      if (!hasId) {
        btn.disabled = true; // 缺 id 的脏数据不可删除
      } else {
        btn.addEventListener('click', () => onDeleteDoc(docId, tdTitle.textContent, item.chunk_count));
      }
      tdAction.appendChild(btn);

      tr.appendChild(tdAction);
      tbody.appendChild(tr);
    }
  }

  async function onEditDoc(docId) {
    try {
      const detail = await apiFetch('/api/documents/' + encodeURIComponent(docId));
      if (!detail || detail.id === undefined || detail.id === null) {
        showError('加载文档 #' + docId + ' 失败：响应缺少文档信息。');
        return;
      }
      ui['doc-title-input'].value = String(detail.title || '');
      ui['doc-title-input'].disabled = true; // 锁定标题：保证提交按同名覆盖更新本文档
      ui['doc-content-textarea'].value = String(detail.content || '');
      ui['doc-file-input'].value = '';
      setFileHint('');
      clearError();
      showSuccess(
        '已加载「' + detail.title + '」（文档 #' + docId + '）：修改正文后提交将覆盖更新原文档' +
          '（如需改标题请取消编辑后新建）。'
      );
      const form = ui['doc-form'];
      if (form && typeof form.scrollIntoView === 'function') {
        form.scrollIntoView({ block: 'start' });
      }
    } catch (err) {
      showError('加载文档 #' + docId + ' 失败：' + errMessage(err));
    }
  }

  function renderDocTableEmpty() {
    const tbody = ui['doc-table-body'];
    tbody.textContent = '';
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = DOC_TABLE_COLSPAN;
    td.className = 'table-empty';
    td.textContent = '暂无文档。可在上方表单录入或上传文件；若后端已启动仍未显示，请检查接口是否可用。';
    tr.appendChild(td);
    tbody.appendChild(tr);
  }

  /* ---------------- 分片查看（GET /api/documents/{id}/chunks，行下方展开/收起） ---------------- */

  async function onToggleChunks(docRow, docId, btn) {
    const next = docRow.nextElementSibling;
    if (next && next.classList.contains('chunk-row')) {
      next.remove(); // 已展开 → 收起
      btn.textContent = '查看分片';
      return;
    }

    const expandRow = document.createElement('tr');
    expandRow.className = 'chunk-row';
    const cell = document.createElement('td');
    cell.colSpan = DOC_TABLE_COLSPAN;
    cell.textContent = '分片加载中…';
    expandRow.appendChild(cell);
    docRow.after(expandRow);
    btn.textContent = '收起分片';
    btn.disabled = true; // 加载期间禁止重复点击

    try {
      const chunks = await apiFetch('/api/documents/' + encodeURIComponent(docId) + '/chunks');
      cell.textContent = '';
      renderChunksPanel(cell, docId, Array.isArray(chunks) ? chunks : []);
    } catch (err) {
      cell.textContent = '';
      const p = document.createElement('div');
      p.className = 'chunk-load-error';
      p.textContent = '分片加载失败：' + errMessage(err) + '（可收起后重试）';
      cell.appendChild(p);
    } finally {
      btn.disabled = false;
    }
  }

  function renderChunksPanel(container, docId, chunks) {
    const panel = document.createElement('div');
    panel.className = 'chunks-panel';

    const head = document.createElement('div');
    head.className = 'chunks-head';
    const headId =
      chunks.length > 0 && chunks[0] && chunks[0].doc_id !== undefined && chunks[0].doc_id !== null
        ? chunks[0].doc_id
        : docId;
    head.textContent = '文档 #' + headId + ' 共 ' + chunks.length + ' 个分片';
    panel.appendChild(head);

    if (chunks.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'chunks-empty';
      empty.textContent = '暂无分片数据。';
      panel.appendChild(empty);
    } else {
      const list = document.createElement('div');
      list.className = 'chunk-list';
      for (const chunk of chunks) {
        list.appendChild(renderChunkItem(chunk));
      }
      panel.appendChild(list);
    }
    container.appendChild(panel);
  }

  function renderChunkItem(chunk) {
    const item = chunk || {};
    const seq = item.seq === null || item.seq === undefined ? '—' : String(item.seq);
    const sectionPath =
      item.section_path === null || item.section_path === undefined || String(item.section_path) === ''
        ? ''
        : String(item.section_path);
    const text = item.text === null || item.text === undefined ? '' : String(item.text);

    const node = document.createElement('div');
    node.className = 'chunk-item';

    const meta = document.createElement('div');
    meta.className = 'chunk-meta';
    const seqSpan = document.createElement('span');
    seqSpan.className = 'chunk-seq';
    seqSpan.textContent = 'seq ' + seq;
    meta.appendChild(seqSpan);
    if (item.id !== null && item.id !== undefined) {
      const idSpan = document.createElement('span');
      idSpan.className = 'chunk-id';
      idSpan.textContent = ' #' + String(item.id);
      meta.appendChild(idSpan);
    }
    if (sectionPath !== '') {
      const pathSpan = document.createElement('span');
      pathSpan.className = 'chunk-path';
      pathSpan.textContent = ' · ' + sectionPath;
      meta.appendChild(pathSpan);
    }
    node.appendChild(meta);

    const textDiv = document.createElement('div');
    textDiv.className = 'chunk-text';
    textDiv.textContent = text === '' ? '（空文本）' : text;
    node.appendChild(textDiv);

    if (text.length > CHUNK_PREVIEW_CHARS) {
      textDiv.classList.add('collapsed'); // 默认截断（max-height），点按钮展开全文
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'btn-toggle-chunk';
      toggle.textContent = '展开全文';
      toggle.addEventListener('click', () => {
        const collapsed = textDiv.classList.toggle('collapsed');
        toggle.textContent = collapsed ? '展开全文' : '收起全文';
      });
      node.appendChild(toggle);
    }
    return node;
  }

  /* ---------------- 录入文档（POST /api/documents；手工录入与文件上传共用） ---------------- */

  async function onSubmitDoc(e) {
    e.preventDefault();
    const title = ui['doc-title-input'].value.trim();
    const content = ui['doc-content-textarea'].value;
    if (title === '' || content.trim() === '') {
      showError('标题与正文均不能为空。');
      return;
    }

    ui['submit-doc-btn'].disabled = true;
    try {
      const res = await apiFetch('/api/documents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: title, content: content }),
      });
      const data = res || {};
      const idText = data.id !== undefined && data.id !== null ? '（文档 #' + data.id + '）' : '';
      const countText =
        typeof data.chunk_count === 'number' ? '，共 ' + data.chunk_count + ' 个切片' : '';
      showSuccess('已入库' + idText + countText);
      ui['doc-title-input'].disabled = false;
      ui['doc-title-input'].value = '';
      ui['doc-content-textarea'].value = '';
      ui['doc-file-input'].value = ''; // 清空文件选择，便于再次选择同名文件也能触发 change
      setFileHint('');
      loadDocuments(); // 失败走其自身 catch 并显示
    } catch (err) {
      showError('录入失败：' + errMessage(err));
    } finally {
      ui['submit-doc-btn'].disabled = false;
    }
  }

  /* ---------------- 删除文档（DELETE /api/documents/{id}） ---------------- */

  async function onDeleteDoc(docId, title, chunkCount) {
    const safeTitle = title && title !== '（无标题）' ? title : '#' + docId;
    const countText = typeof chunkCount === 'number' ? String(chunkCount) : '未知数量';
    const confirmed = window.confirm(
      '确认删除文档「' + safeTitle + '」？将同时删除其 ' + countText + ' 个切片，操作不可恢复。'
    );
    if (!confirmed) return;

    try {
      const res = await apiFetch('/api/documents/' + encodeURIComponent(docId), { method: 'DELETE' });
      const data = res || {};
      if (data.deleted === false) {
        throw new Error('后端返回删除未成功');
      }
      const removedText =
        typeof data.chunks_removed === 'number' ? '，移除切片 ' + data.chunks_removed + ' 个' : '';
      showSuccess('已删除文档 #' + docId + removedText);
      loadDocuments();
    } catch (err) {
      showError('删除文档 #' + docId + ' 失败：' + errMessage(err));
    }
  }
})();
