/* 员工端单页聊天：SSE 流式 + 打字机 + 引用来源 + 草稿确认。
 *
 * 为什么手写 SSE 解析而不用 EventSource：
 * 1) EventSource 只能发 GET，问题文本长了拼 URL 不现实；
 * 2) 我们需要在同一个请求里带 JSON body 和 Cookie 凭证；
 * 3) fetch + ReadableStream 能拿到原始分块，自己按 "\n\n" 切帧即可，逻辑不超过 20 行。
 */
(() => {
  const $ = (sel) => document.querySelector(sel);

  const state = {
    sessionId: null,
    streaming: false,
    pendingConfirm: null,
  };

  /* ------------------------------------------------------------ 工具函数 */
  const el = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const scrollToBottom = () => {
    const box = $('#messages');
    box.scrollTop = box.scrollHeight;
  };

  async function api(url, options = {}) {
    const resp = await fetch(url, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.ok === false) {
      throw new Error(data.message || `请求失败（HTTP ${resp.status}）`);
    }
    return data;
  }

  /* ------------------------------------------------------------ 渲染消息 */
  function renderCitations(wrapper, items) {
    if (!items || !items.length) return;
    const details = el('details', 'citations');
    const summary = el('summary', null, `📎 引用制度条文（${items.length} 条）`);
    details.appendChild(summary);
    items.forEach((c, i) => {
      const row = el('div', 'cite-row');
      const head = el('div', 'cite-head');
      head.innerHTML = `<span class="cite-idx">${i + 1}</span>
        <span class="cite-title">《${c.doc_title || c.doc}》${c.heading || ''}</span>
        <span class="cite-score">score ${Number(c.score || 0).toFixed(3)}</span>`;
      row.appendChild(head);
      row.appendChild(el('p', 'cite-text', c.text || ''));
      if (c.version) row.appendChild(el('p', 'cite-meta', `制度版本 ${c.version}${c.doc ? ' · 文件 ' + c.doc : ''}`));
      details.appendChild(row);
    });
    wrapper.appendChild(details);
  }

  function renderMeta(wrapper, meta) {
    if (!meta) return;
    const parts = [];
    if (meta.rounds) parts.push(`推理 ${meta.rounds} 轮`);
    if (meta.tools && meta.tools.length) parts.push(`工具：${meta.tools.join(' → ')}`);
    if (meta.latency_ms !== undefined) parts.push(`${meta.latency_ms} ms`);
    if (meta.mode) parts.push(meta.mode === 'heuristic' ? '离线规则引擎' : meta.model || meta.mode);
    if (!parts.length) return;
    wrapper.appendChild(el('div', 'msg-meta', parts.join(' · ')));
  }

  function addUserMessage(text) {
    const row = el('div', 'msg msg-user');
    row.appendChild(el('div', 'bubble', text));
    $('#messages').appendChild(row);
    scrollToBottom();
  }

  function addAssistantShell() {
    const row = el('div', 'msg msg-assistant');
    const bubble = el('div', 'bubble');
    const status = el('div', 'status-line', '正在检索制度…');
    const body = el('div', 'answer');
    bubble.appendChild(status);
    bubble.appendChild(body);
    row.appendChild(bubble);
    $('#messages').appendChild(row);
    scrollToBottom();
    return { row, bubble, status, body };
  }

  /* ------------------------------------------------------------ 打字机 */
  function createTypewriter(node, speedMs = 12, charsPerTick = 2) {
    let queue = '';
    let timer = null;
    const flush = () => {
      if (!queue) {
        clearInterval(timer);
        timer = null;
        return;
      }
      node.textContent += queue.slice(0, charsPerTick);
      queue = queue.slice(charsPerTick);
      scrollToBottom();
    };
    return {
      push(text) {
        queue += text;
        if (!timer) timer = setInterval(flush, speedMs);
      },
      finish() {
        // 立刻把剩余内容吐完，避免回答已结束但屏幕上还在慢慢打字
        if (timer) clearInterval(timer);
        timer = null;
        node.textContent += queue;
        queue = '';
        scrollToBottom();
      },
    };
  }

  /* ------------------------------------------------------------ 草稿确认卡片 */
  function showConfirmCard(draft) {
    state.pendingConfirm = draft;
    const slot = $('#confirm-slot');
    slot.hidden = false;
    slot.innerHTML = '';

    const card = el('div', 'confirm-card');
    card.appendChild(el('div', 'confirm-head', '🧾 待确认的报销单草稿'));

    const info = el('div', 'confirm-info');
    info.innerHTML = `
      <div><span class="k">出差城市</span><span class="v">${draft.trip_city || '未填'}</span></div>
      <div><span class="k">起止日期</span><span class="v">${draft.trip_start || '—'} ~ ${draft.trip_end || '—'}</span></div>
      <div><span class="k">备注</span><span class="v">${draft.note || '—'}</span></div>`;
    card.appendChild(info);

    const items = draft.items || [];
    if (items.length) {
      const table = el('table', 'table confirm-table');
      table.innerHTML = `<thead><tr><th>科目</th><th>金额</th><th>发票号</th><th>日期</th></tr></thead>
        <tbody>${items.map((it) => `<tr>
          <td>${it.category || '其他'}</td>
          <td>¥${Number(it.amount || 0).toFixed(2)}</td>
          <td>${it.invoice_no || '—'}</td>
          <td>${it.invoice_date || '—'}</td></tr>`).join('')}</tbody>`;
      card.appendChild(table);
      // 合计与审批链路由后端算好（后端的 rules.approval_chain 才是唯一口径），前端只兜底计算
      const total = typeof draft.total === 'number'
        ? draft.total
        : items.reduce((sum, it) => sum + Number(it.amount || 0), 0);
      card.appendChild(el('div', 'confirm-total', `合计 ¥${total.toFixed(2)}`));
      if (Array.isArray(draft.approval_chain) && draft.approval_chain.length) {
        card.appendChild(el('div', 'confirm-chain',
          `审批链路：${draft.approval_chain.join(' → ')}${draft.approval_clause ? `（${draft.approval_clause}）` : ''}`));
      }
    }

    card.appendChild(el('p', 'confirm-tip', '确认后才会真正生成报销单（系统不允许跳过发票校验直接建单）。'));

    const actions = el('div', 'confirm-actions');
    const ok = el('button', 'btn btn-primary', '确认生成');
    const cancel = el('button', 'btn btn-ghost', '取消');
    ok.onclick = () => submitConfirm(true);
    cancel.onclick = () => submitConfirm(false);
    actions.appendChild(ok);
    actions.appendChild(cancel);
    card.appendChild(actions);

    slot.appendChild(card);
    slot.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function hideConfirmCard() {
    state.pendingConfirm = null;
    const slot = $('#confirm-slot');
    slot.hidden = true;
    slot.innerHTML = '';
  }

  async function submitConfirm(accepted) {
    if (!state.sessionId || state.streaming) return;
    const buttons = document.querySelectorAll('.confirm-actions button');
    buttons.forEach((b) => (b.disabled = true));
    try {
      const data = await api('/api/chat/confirm', {
        method: 'POST',
        body: JSON.stringify({ session_id: state.sessionId, accepted }),
      });
      hideConfirmCard();
      const shell = addAssistantShell();
      shell.status.remove();
      shell.body.textContent = data.message || (accepted ? '已生成报销单草稿。' : '已取消。');
      await refreshSessions();
      scrollToBottom();
    } catch (err) {
      buttons.forEach((b) => (b.disabled = false));
      alert(err.message || '确认失败');
    }
  }

  /* ------------------------------------------------------------ 事件处理 */
  /* 每轮对话的状态都收敛在 ctx 里，handleEvent 只负责根据事件类型更新 UI。 */
  function handleEvent(evt, ctx) {
    const { shell, typer } = ctx;
    switch (evt.type) {
      case 'status':
        shell.status.textContent = evt.message || '处理中…';
        break;
      case 'tool_call': {
        const line = el('div', 'tool-line',
          `🔧 调用工具 ${evt.name}　${JSON.stringify(evt.args || {}).slice(0, 120)}`);
        shell.bubble.insertBefore(line, shell.body);
        scrollToBottom();
        break;
      }
      case 'tool_result': {
        const line = el('div', `tool-line ${evt.ok ? 'ok' : 'fail'}`,
          `${evt.ok ? '✓' : '✗'} ${evt.name}：${evt.summary || ''}`);
        shell.bubble.insertBefore(line, shell.body);
        scrollToBottom();
        break;
      }
      case 'token':
        shell.status.remove();
        typer.push(evt.text || '');
        break;
      case 'citations':
        if (!ctx.citationsBox) ctx.citationsBox = el('div', 'citations-wrap');
        renderCitations(ctx.citationsBox, evt.items);
        shell.bubble.appendChild(ctx.citationsBox);
        break;
      case 'confirm_required':
        ctx.confirmShown = true;
        shell.status.remove();
        typer.finish();
        showConfirmCard(evt.draft || {});
        break;
      case 'done':
        typer.finish();
        if (evt.meta) renderMeta(shell.bubble, evt.meta);
        if (!ctx.confirmShown) shell.status.remove();
        break;
      case 'error':
        typer.finish();
        shell.status.remove();
        shell.bubble.appendChild(el('div', 'msg-error', `⚠️ ${evt.message || '服务异常'}`));
        break;
      default:
        break;
    }
  }

  /* ------------------------------------------------------------ 发送与流式接收 */
  async function send(question) {
    if (state.streaming) return;
    if (!state.sessionId) {
      await createSession();
    }

    state.streaming = true;
    $('#send').disabled = true;
    $('#messages').querySelector('.welcome')?.remove();

    addUserMessage(question);
    const shell = addAssistantShell();
    const typer = createTypewriter(shell.body);
    const ctx = { shell, typer, citationsBox: null, confirmShown: false };

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: state.sessionId, question }),
      });

      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.message || `请求失败（HTTP ${resp.status}）`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE 帧以空行分隔；最后一段可能不完整，留在 buffer 里等下一块
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';

        for (const frame of frames) {
          const line = frame.split('\n').find((l) => l.startsWith('data: '));
          if (!line) continue;
          let evt;
          try {
            evt = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          handleEvent(evt, ctx);
        }
      }
      typer.finish();
      await refreshSessions();
    } catch (err) {
      typer.finish();
      shell.status.remove();
      const tip = el('div', 'msg-error', `⚠️ ${err.message || '网络异常，请重试'}`);
      shell.bubble.appendChild(tip);
    } finally {
      state.streaming = false;
      $('#send').disabled = false;
      $('#question').focus();
    }
  }

  /* ------------------------------------------------------------ 会话管理 */
  async function refreshSessions() {
    try {
      const data = await api('/api/sessions');
      const list = $('#session-list');
      list.innerHTML = '';
      (data.sessions || []).forEach((s) => {
        const item = el('div', 'session-item' + (s.id === state.sessionId ? ' active' : ''));
        item.dataset.id = s.id;
        item.appendChild(el('span', 'session-title', s.title));
        item.appendChild(el('span', 'session-meta', `${s.n_msgs} 条`));
        item.onclick = () => selectSession(s.id);
        list.appendChild(item);
      });
      if (!(data.sessions || []).length) {
        list.appendChild(el('p', 'empty-hint', '还没有会话，点上面的按钮开始。'));
      }
    } catch {
      /* 列表刷新失败不打断对话 */
    }
  }

  async function createSession() {
    const data = await api('/api/sessions', { method: 'POST', body: '{}' });
    state.sessionId = data.session.id;
    await refreshSessions();
    clearMessages();
    return state.sessionId;
  }

  function clearMessages() {
    $('#messages').innerHTML = '';
    $('#chat-title').textContent = '制度问答';
    hideConfirmCard();
  }

  async function selectSession(id) {
    if (state.streaming) return;
    state.sessionId = Number(id);
    document.querySelectorAll('.session-item').forEach((node) => {
      node.classList.toggle('active', Number(node.dataset.id) === state.sessionId);
    });
    clearMessages();

    try {
      const data = await api(`/api/sessions/${id}/messages`);
      const box = $('#messages');
      (data.messages || []).forEach((m) => {
        if (m.role === 'user') {
          addUserMessage(m.content);
        } else {
          const row = el('div', 'msg msg-assistant');
          const bubble = el('div', 'bubble');
          bubble.appendChild(el('div', 'answer', m.content));
          if (m.citations && m.citations.length) {
            const wrap = el('div', 'citations-wrap');
            renderCitations(wrap, m.citations);
            bubble.appendChild(wrap);
          }
          renderMeta(bubble, m.meta);
          row.appendChild(bubble);
          box.appendChild(row);
        }
      });
      if (!(data.messages || []).length) {
        box.innerHTML = '<div class="welcome"><h2>新的会话</h2><p>直接提问，或点下面的示例试试。</p></div>';
      }
      if (data.pending && data.pending.pending_draft) {
        showConfirmCard(data.pending.pending_draft.args);
      }
      const first = (data.messages || []).find((m) => m.role === 'user');
      if (first) $('#chat-title').textContent = first.content.slice(0, 24);
      scrollToBottom();
    } catch (err) {
      $('#messages').innerHTML = `<div class="msg-error">⚠️ ${err.message}</div>`;
    }
  }

  /* ------------------------------------------------------------ 事件绑定 */
  function bind() {
    $('#new-session').onclick = () => createSession().catch((e) => alert(e.message));

    $('#composer').onsubmit = (event) => {
      event.preventDefault();
      const input = $('#question');
      const text = input.value.trim();
      if (!text) return;
      input.value = '';
      input.style.height = 'auto';
      send(text);
    };

    $('#question').addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        $('#composer').requestSubmit();
      }
    });

    $('#question').addEventListener('input', (event) => {
      const node = event.target;
      node.style.height = 'auto';
      node.style.height = `${Math.min(node.scrollHeight, 140)}px`;
    });

    document.querySelectorAll('.chip').forEach((chip) => {
      chip.onclick = () => send(chip.dataset.q);
    });

    document.querySelectorAll('.session-item').forEach((node) => {
      node.onclick = () => selectSession(node.dataset.id);
    });
  }

  async function boot() {
    bind();
    const first = document.querySelector('.session-item');
    if (first) {
      await selectSession(first.dataset.id);
    } else {
      $('#question').focus();
    }
  }

  boot();
})();
