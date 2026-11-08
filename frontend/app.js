/* app.js —— 聊天页逻辑（原生 ES6 + fetch；无框架、无构建、无 CDN 外链）
 * 接口契约：requirements/47846874/design.md『RESTful 契约』（冻结，不臆造接口）
 * 本页调用端点：
 *   GET  /api/health              → 顶部后端连接状态指示
 *   GET  /api/conversations       → 左侧会话列表（id/label/created_at/message_count）
 *   GET  /api/conversations/{id}  → 点击会话加载历史（messages: id/role/content/sources/created_at）
 *   POST /api/chat                → 发送问题（{question, conversation_id?}）
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'spd_chat_current_conversation_id';

  const state = {
    currentConversationId: null, // 本地记录当前会话 id；null 表示下一条提问由后端自动新建会话
    sending: false,
  };

  const ui = {}; // id -> DOM 节点；id 列表见 DOM_IDS，必须与 index.html 中定义一致

  const DOM_IDS = [
    'backend-status',
    'new-conversation-btn',
    'conversation-list',
    'error-banner',
    'messages',
    'question-input',
    'send-btn',
  ];

  document.addEventListener('DOMContentLoaded', init);

  function init() {
    for (const id of DOM_IDS) {
      ui[id] = document.getElementById(id);
    }
    const missing = DOM_IDS.filter((id) => !ui[id]);
    if (missing.length > 0) {
      console.error('[app.js] index.html 缺少以下 DOM id：' + missing.join(', '));
      return; // 不绑定事件，避免对空节点操作抛未捕获异常
    }

    state.currentConversationId = loadCurrentId();

    ui['new-conversation-btn'].addEventListener('click', newConversation);
    ui['send-btn'].addEventListener('click', sendMessage);
    ui['question-input'].addEventListener('keydown', onInputKeydown);

    checkHealth();
    loadConversations();
    renderEmptyHint('输入问题开始提问；点击左侧会话可查看历史记录。');
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
    ui['error-banner'].textContent = message;
    ui['error-banner'].classList.remove('hidden');
  }

  function clearError() {
    ui['error-banner'].textContent = '';
    ui['error-banner'].classList.add('hidden');
  }

  /* ---------------- 格式化（空值/异常值容错） ---------------- */

  function formatScore(score) {
    const n = Number(score);
    if (score !== null && score !== undefined && score !== '' && isFinite(n)) {
      return n.toFixed(4);
    }
    return '—';
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

  /* ---------------- 健康检查（GET /api/health） ---------------- */

  async function checkHealth() {
    const badge = ui['backend-status'];
    try {
      const body = await apiFetch('/api/health');
      if (!body || body.status !== 'ok') {
        throw new Error('响应字段异常');
      }
      badge.textContent = '后端已连接';
      badge.className = 'status status-ok';
    } catch (err) {
      badge.textContent = '后端未连接';
      badge.className = 'status status-bad';
      badge.title = errMessage(err);
    }
  }

  /* ---------------- 会话列表（GET /api/conversations） ---------------- */

  async function loadConversations() {
    try {
      const list = await apiFetch('/api/conversations');
      renderConversationList(Array.isArray(list) ? list : []);
    } catch (err) {
      renderConversationList([]);
      showError('加载会话列表失败：' + errMessage(err));
    }
  }

  function renderConversationList(list) {
    const ul = ui['conversation-list'];
    ul.textContent = '';
    if (!Array.isArray(list) || list.length === 0) {
      const li = document.createElement('li');
      li.className = 'conversation-empty';
      li.textContent = '暂无会话';
      ul.appendChild(li);
      return;
    }
    for (const conv of list) {
      const item = conv || {};
      const id = item.id;
      if (id === undefined || id === null) continue; // 契约必有 id，防御性跳过脏数据
      const li = document.createElement('li');
      li.className = 'conversation-item';
      li.dataset.conversationId = String(id);

      const label = document.createElement('div');
      label.className = 'conversation-label';
      label.textContent =
        item.label !== null && item.label !== undefined && String(item.label).trim() !== ''
          ? String(item.label)
          : '会话 #' + id;

      const meta = document.createElement('div');
      meta.className = 'conversation-meta';
      const count = typeof item.message_count === 'number' ? String(item.message_count) : '—';
      meta.textContent = count + ' 条消息 · ' + formatTime(item.created_at);

      li.appendChild(label);
      li.appendChild(meta);
      li.addEventListener('click', () => openConversation(id));
      ul.appendChild(li);
    }
    highlightConversation(state.currentConversationId);
  }

  function highlightConversation(id) {
    const items = ui['conversation-list'].querySelectorAll('.conversation-item');
    items.forEach((li) => {
      if (li.dataset.conversationId === String(id)) {
        li.classList.add('active');
      } else {
        li.classList.remove('active');
      }
    });
  }

  /* ---------------- 历史消息（GET /api/conversations/{id}） ---------------- */

  async function openConversation(id) {
    try {
      const conv = await apiFetch('/api/conversations/' + encodeURIComponent(id));
      state.currentConversationId = id;
      saveCurrentId(id);
      clearError();
      renderConversationMessages(conv);
      highlightConversation(id);
    } catch (err) {
      showError('加载会话 #' + id + ' 失败：' + errMessage(err));
    }
  }

  function renderConversationMessages(conv) {
    const data = conv || {};
    const messages = Array.isArray(data.messages) ? data.messages : [];
    if (messages.length === 0) {
      renderEmptyHint('该会话暂无消息。');
      return;
    }
    for (const msg of messages) {
      const m = msg || {};
      const role = m.role === 'user' ? 'user' : 'assistant';
      // 契约：历史消息字段为 id/role/content/sources/created_at，不含 mode，仅渲染内容与来源
      appendBubble(role, m.content, Array.isArray(m.sources) ? m.sources : [], null, m.created_at);
    }
  }

  /* ---------------- 消息渲染 ---------------- */

  function renderEmptyHint(text) {
    const wrap = ui['messages'];
    wrap.textContent = '';
    const hint = document.createElement('div');
    hint.className = 'empty-hint';
    hint.textContent = text;
    wrap.appendChild(hint);
  }

  function appendBubble(role, content, sources, mode, createdAt) {
    const wrap = ui['messages'];
    const hint = wrap.querySelector('.empty-hint');
    if (hint) hint.remove();

    const isUser = role === 'user';
    const bubble = document.createElement('div');
    bubble.className = 'bubble bubble-' + (isUser ? 'user' : 'assistant');

    const roleTag = document.createElement('div');
    roleTag.className = 'bubble-role';
    roleTag.textContent = (isUser ? '用户' : '助手') + (createdAt ? ' · ' + formatTime(createdAt) : '');
    bubble.appendChild(roleTag);

    const bodyEl = document.createElement('div');
    bodyEl.className = 'bubble-content';
    bodyEl.textContent = content === null || content === undefined ? '' : String(content);
    bubble.appendChild(bodyEl);

    if (!isUser && mode) {
      const badge = buildModeBadge(mode);
      if (badge) bubble.appendChild(badge);
    }

    if (!isUser && Array.isArray(sources) && sources.length > 0) {
      bubble.appendChild(buildSources(sources));
    }

    wrap.appendChild(bubble);
    wrap.scrollTop = wrap.scrollHeight;
  }

  // 契约 mode：'llm' | 'retrieval_only' | 'refusal'；llm 不显示特殊标注
  function buildModeBadge(mode) {
    if (mode === 'retrieval_only') {
      const badge = document.createElement('div');
      badge.className = 'mode-badge mode-retrieval-only';
      badge.textContent = '⚠ 未接入生成模型，以下为知识库原文检索';
      return badge;
    }
    if (mode === 'refusal') {
      const badge = document.createElement('div');
      badge.className = 'mode-badge mode-refusal';
      badge.textContent = '知识库中未找到';
      return badge;
    }
    return null;
  }

  function buildSources(sources) {
    const box = document.createElement('div');
    box.className = 'sources';

    const title = document.createElement('div');
    title.className = 'sources-title';
    title.textContent = '来源：';
    box.appendChild(title);

    const ul = document.createElement('ul');
    for (const src of sources) {
      const s = src || {};
      const name = s.doc_title === null || s.doc_title === undefined ? '未知文档' : String(s.doc_title);
      const seq = s.seq === null || s.seq === undefined ? '?' : String(s.seq);
      const li = document.createElement('li');

      const line = document.createElement('span');
      line.className = 'source-name';
      line.textContent = name + '#' + seq + '（相似度 ' + formatScore(s.score) + '）';
      li.appendChild(line);

      const extras = [];
      if (s.section_path !== null && s.section_path !== undefined && String(s.section_path) !== '') {
        extras.push(String(s.section_path));
      }
      if (s.snippet !== null && s.snippet !== undefined && String(s.snippet) !== '') {
        extras.push(String(s.snippet));
      }
      if (extras.length > 0) {
        const detail = document.createElement('div');
        detail.className = 'source-detail';
        detail.textContent = extras.join(' · ');
        li.appendChild(detail);
      }
      ul.appendChild(li);
    }
    box.appendChild(ul);
    return box;
  }

  /* ---------------- 发送（POST /api/chat） ---------------- */

  function onInputKeydown(e) {
    if (e.key !== 'Enter') return;
    if (e.shiftKey) return; // Shift+Enter 换行
    if (e.isComposing || e.keyCode === 229) return; // 中文输入法组词过程中不发送
    e.preventDefault();
    sendMessage();
  }

  async function sendMessage() {
    if (state.sending) return;
    const input = ui['question-input'];
    const question = input.value.trim();
    if (question === '') {
      showError('请输入问题后再发送。');
      input.focus();
      return;
    }

    clearError();
    setSending(true);

    const payload = { question: question };
    if (state.currentConversationId !== null) {
      payload.conversation_id = state.currentConversationId; // 本地记录的会话续聊
    }

    try {
      const res = await apiFetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = res || {};
      appendBubble('user', question, [], null, null);
      appendBubble(
        'assistant',
        data.answer === null || data.answer === undefined ? '' : data.answer,
        Array.isArray(data.sources) ? data.sources : [],
        data.mode || null,
        null
      );
      if (typeof data.conversation_id === 'number') {
        state.currentConversationId = data.conversation_id;
        saveCurrentId(data.conversation_id);
        highlightConversation(data.conversation_id);
      }
      input.value = '';
      loadConversations(); // 静默刷新会话列表；失败走其自身 catch 并显示
    } catch (err) {
      showError('提问失败：' + errMessage(err));
    } finally {
      setSending(false);
      input.focus();
    }
  }

  function setSending(flag) {
    state.sending = flag;
    ui['send-btn'].disabled = flag;
    ui['send-btn'].textContent = flag ? '发送中…' : '发送';
  }

  /* ---------------- 新建会话（本地切换，不依赖额外后端端点） ---------------- */

  function newConversation() {
    state.currentConversationId = null;
    try {
      window.sessionStorage.removeItem(STORAGE_KEY);
    } catch (err) {
      /* 存储不可用时忽略，仅影响刷新后的记忆 */
    }
    clearError();
    renderEmptyHint('已开始新会话，输入问题即可提问。');
    highlightConversation(null);
    ui['question-input'].focus();
  }

  function saveCurrentId(id) {
    try {
      window.sessionStorage.setItem(STORAGE_KEY, String(id));
    } catch (err) {
      /* 隐私模式等场景可能不可用，忽略 */
    }
  }

  function loadCurrentId() {
    try {
      const raw = window.sessionStorage.getItem(STORAGE_KEY);
      if (raw === null) return null;
      const n = Number(raw);
      return Number.isInteger(n) && n > 0 ? n : null;
    } catch (err) {
      return null;
    }
  }
})();
