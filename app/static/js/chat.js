
(function () {
  'use strict';

  var messagesEl = document.getElementById('messages');
  var chatBody   = document.getElementById('chat-body');
  var input      = document.getElementById('input');
  var sendBtn    = document.getElementById('send');
  var topbarTitle = document.getElementById('topbar-title');
  var manageBtn   = document.getElementById('manage-participants-btn');
  var threadListBody = document.getElementById('thread-list-body');

  // ── Thread state ───────────────────────────────────────────────────────────
  var currentThreadId = null;   // null = General chat
  var threadCache     = [];     // [{id, name, ...}]
  var unreadSet       = {};     // thread_id -> true

  // ── Permissions (set via data attributes from server) ──────────────────────
  var permsEl = document.getElementById('chat-perms');
  var canIndividual = permsEl ? permsEl.getAttribute('data-can-individual') === '1' : true;
  var canGroup      = permsEl ? permsEl.getAttribute('data-can-group') === '1' : true;
  var myUsername    = permsEl ? permsEl.getAttribute('data-username') || '' : '';

  // ── Markdown renderer ─────────────────────────────────────────────────────
  var md = (typeof marked !== 'undefined') ? marked : null;
  if (md && md.setOptions) {
    md.setOptions({ breaks: true, gfm: true });
  }

  function renderMarkdown(text) {
    if (!md) return escapeHtml(text).replace(/\\n/g, '<br>');
    try {
      var html = md.parse ? md.parse(text) : md(text);
      return html;
    } catch(e) { return escapeHtml(text); }
  }

  function escapeHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  }

  function addCopyButtons(container) {
    var pres = container.querySelectorAll('pre');
    pres.forEach(function(pre) {
      var code = pre.querySelector('code');
      if (!code) return;
      var btn = document.createElement('button');
      btn.className = 'copy-btn';
      btn.textContent = 'Copy';
      btn.onclick = function() {
        navigator.clipboard.writeText(code.innerText).then(function() {
          btn.textContent = 'Copied!';
          setTimeout(function() { btn.textContent = 'Copy'; }, 1500);
        }).catch(function() {});
      };
      pre.style.position = 'relative';
      pre.appendChild(btn);
    });
  }

  // ── Timestamp formatting ───────────────────────────────────────────────────
  function fmtTime(d) {
    if (!d) return '';
    var now = new Date();
    var todayStr  = now.toDateString();
    var yest = new Date(now); yest.setDate(yest.getDate() - 1);
    var hh = String(d.getHours()).padStart(2,'0');
    var mm = String(d.getMinutes()).padStart(2,'0');
    var time = hh + ':' + mm;
    if (d.toDateString() === todayStr)       return time;
    if (d.toDateString() === yest.toDateString()) return 'Yesterday ' + time;
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return d.getDate() + ' ' + months[d.getMonth()] + ' ' + time;
  }

  function dateSepLabel(isoDate) {
    var d = new Date(isoDate + 'T00:00:00');
    var now = new Date();
    var todayStr  = now.toDateString();
    var yest = new Date(now); yest.setDate(yest.getDate() - 1);
    if (d.toDateString() === todayStr)     return 'Today';
    if (d.toDateString() === yest.toDateString()) return 'Yesterday';
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return d.getDate() + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
  }

  // ── Chat history (General thread) ──────────────────────────────────────────
  var currentUser = '';

  function loadGeneralHistory() {
    chatBody.innerHTML = '';
    fetch('/api/history')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        if (data.current_user) currentUser = data.current_user.toLowerCase();
        if (!data.messages || !data.messages.length) return;
        var msgs = data.messages;
        var lastDateLabel = null;
        for (var i = 0; i < msgs.length; i++) {
          var m = msgs[i];
          var sessionDate = m._session_date || null;
          if (sessionDate) {
            var label = dateSepLabel(sessionDate);
            if (label !== lastDateLabel) {
              var dsep = document.createElement('div');
              dsep.className = 'date-sep';
              dsep.innerHTML = '<span>' + label + '</span>';
              chatBody.appendChild(dsep);
              lastDateLabel = label;
            }
          }
          var msgTs = m._ts ? new Date(m._ts) : null;
          var msgUser = m._user || '';
          if (m.role === 'user') {
            var isMe = msgUser.toLowerCase() === currentUser;
            appendUserRow(m.content, msgTs, msgUser, isMe);
          } else if (m.role === 'assistant' && m.content) {
            var el = appendAgentRow(msgTs);
            el.innerHTML = renderMarkdown(m.content);
            addCopyButtons(el);
          }
        }
        var sep = document.createElement('div');
        sep.className = 'msg-row system-row';
        sep.innerHTML = '<span class="system-text" style="opacity:.5;font-size:.75rem;">\\u2014 earlier messages above \\u2014</span>';
        chatBody.appendChild(sep);
        messagesEl.scrollTop = messagesEl.scrollHeight;
      })
      .catch(function() {});
  }

  // ── Thread message history ─────────────────────────────────────────────────
  function loadThreadMessages(threadId) {
    chatBody.innerHTML = '';
    fetch('/api/threads/' + threadId + '/messages')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data || !data.messages) return;
        var msgs = data.messages;
        for (var i = 0; i < msgs.length; i++) {
          var m = msgs[i];
          var msgTs = m.ts ? new Date(m.ts) : null;
          if (m.role === 'user') {
            var isMe = (m.sender || '').toLowerCase() === currentUser;
            appendUserRow(m.content, msgTs, m.sender, isMe);
          } else if (m.role === 'assistant' && m.content) {
            var el = appendAgentRow(msgTs);
            el.innerHTML = renderMarkdown(m.content);
            addCopyButtons(el);
          }
        }
        messagesEl.scrollTop = messagesEl.scrollHeight;
      })
      .catch(function() {});
  }

  // ── Thread list ────────────────────────────────────────────────────────────
  function loadThreads(cb) {
    fetch('/api/threads')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        threadCache = (data && data.threads) ? data.threads : [];
        renderThreadList();
        if (cb) cb();
      })
      .catch(function() {});
  }

  function renderThreadList() {
    threadListBody.innerHTML = '';

    // ── General section (always visible) ──────────────────────────────
    var genHdr = document.createElement('div');
    genHdr.className = 'thread-section-hdr';
    genHdr.textContent = 'General';
    threadListBody.appendChild(genHdr);

    var gen = document.createElement('div');
    gen.className = 'thread-item' + (currentThreadId === null ? ' active' : '');
    gen.setAttribute('tabindex', '0');
    gen.setAttribute('role', 'option');
    if (currentThreadId === null) gen.setAttribute('aria-current', 'true');
    gen.innerHTML = '<span class="thread-item-name">General</span>';
    gen.onclick = function() { selectGeneral(); };
    threadListBody.appendChild(gen);

    var individual = [];
    var group = [];
    for (var i = 0; i < threadCache.length; i++) {
      var t = threadCache[i];
      if (t.participant_count && t.participant_count > 1) {
        group.push(t);
      } else {
        individual.push(t);
      }
    }

    // ── Individual section ────────────────────────────────────────────
    if (canIndividual) {
      var indHdr = document.createElement('div');
      indHdr.className = 'thread-section-hdr';
      indHdr.style.marginTop = '.5rem';
      indHdr.textContent = 'Individual';
      threadListBody.appendChild(indHdr);

      if (individual.length === 0) {
        var empty = document.createElement('div');
        empty.className = 'thread-empty';
        empty.textContent = 'No chats yet \\u2014 click + to start';
        threadListBody.appendChild(empty);
      }
      for (var i = 0; i < individual.length; i++) {
        threadListBody.appendChild(makeThreadItem(individual[i], false));
      }
    }

    // ── Group section ─────────────────────────────────────────────────
    if (canGroup) {
      var grpHdr = document.createElement('div');
      grpHdr.className = 'thread-section-hdr';
      grpHdr.style.marginTop = '.5rem';
      grpHdr.textContent = 'Group';
      threadListBody.appendChild(grpHdr);

      if (group.length === 0) {
        var empty2 = document.createElement('div');
        empty2.className = 'thread-empty';
        empty2.textContent = 'No group threads yet';
        threadListBody.appendChild(empty2);
      }
      for (var i = 0; i < group.length; i++) {
        threadListBody.appendChild(makeThreadItem(group[i], true));
      }
    }

    // ── Hide + button if neither permission ───────────────────────────
    var newBtn = document.getElementById('new-chat-btn');
    if (newBtn) newBtn.style.display = (canIndividual || canGroup) ? '' : 'none';
  }

  function makeThreadItem(t, isGroup) {
    var item = document.createElement('div');
    item.className = 'thread-item' + (currentThreadId === t.id ? ' active' : '');
    item.setAttribute('data-thread-id', t.id);
    item.setAttribute('tabindex', '0');
    item.setAttribute('role', 'option');
    if (currentThreadId === t.id) item.setAttribute('aria-current', 'true');
    var dot = '<span class="unread-dot' + (unreadSet[t.id] ? ' show' : '') + '"></span>';
    var meta = isGroup && t.participant_count ? '<span class="thread-item-meta">' + t.participant_count + '</span>' : '';
    item.innerHTML = dot + '<span class="thread-item-name">' + escapeHtml(t.name) + '</span>' + meta;
    item.onclick = function() { selectThread(t.id, t.name, isGroup); };
    return item;
  }

  function selectGeneral() {
    currentThreadId = null;
    topbarTitle.textContent = 'Chat';
    manageBtn.style.display = 'none';
    resetAgentState();
    renderThreadList();
    loadGeneralHistory();
  }

  function selectThread(threadId, threadName, isGroup) {
    currentThreadId = threadId;
    topbarTitle.textContent = threadName || 'Thread';
    manageBtn.style.display = isGroup ? '' : 'none';
    delete unreadSet[threadId];
    resetAgentState();
    renderThreadList();
    loadThreadMessages(threadId);
  }

  function resetAgentState() {
    chatBody.innerHTML = '';
    currentAgentTextEl = null;
    currentAgentTimeEl = null;
    currentAgentRaw    = '';
    thinkingRowEl      = null;
    input.value        = '';
    input.style.height = '';
    // Re-enable input if WebSocket is connected
    if (ws && ws.readyState === WebSocket.OPEN) {
      input.disabled = false;
      sendBtn.disabled = false;
    }
  }

  // ── WebSocket ─────────────────────────────────────────────────────────────
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var ws = null;

  function connectWs() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.onclose = null;
      ws.close();
    }
    ws = new WebSocket(proto + '//' + location.host + '/ws');

    ws.onopen = function () {
      appendSystem('Connected to CDCN Agent.');
      input.disabled = false;
      sendBtn.disabled = false;
      input.focus();
    };

    ws.onclose = function () {
      appendSystem('Disconnected. Reload to reconnect.');
      input.disabled = true;
      sendBtn.disabled = true;
    };

    ws.onerror = function () { appendSystem('Connection error.'); };

    ws.onmessage = handleWsMessage;
  }

  // ── Message rendering ─────────────────────────────────────────────────────
  var currentAgentTextEl = null;
  var currentAgentRaw    = '';
  var currentAgentTimeEl = null;
  var thinkingRowEl      = null;

  function appendThinkingRow() {
    var row = document.createElement('div');
    row.className = 'msg-row agent-row';
    row.innerHTML =
      '<div class="msg-avatar agent-av">AI</div>' +
      '<div class="msg-col">' +
        '<div class="msg-header"><div class="msg-name">CDCN Agent</div></div>' +
        '<div class="msg-text"><div class="thinking-dots" role="status" aria-label="Agent is thinking"><span></span><span></span><span></span></div></div>' +
      '</div>';
    chatBody.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return row;
  }

  function removeThinkingRow() {
    if (thinkingRowEl) { thinkingRowEl.remove(); thinkingRowEl = null; }
  }

  function handleWsMessage(event) {
    var data;
    try { data = JSON.parse(event.data); } catch (e) { return; }

    var msgThreadId = data.thread_id || null;

    // ── Incoming user message from another participant in a thread ────
    if (data.type === 'thread_msg') {
      if (msgThreadId === currentThreadId) {
        appendUserRow(data.content, new Date(data.ts), data.sender, false);
      } else if (msgThreadId) {
        unreadSet[msgThreadId] = true;
        renderThreadList();
      }
      return;
    }

    // ── Route by thread_id: only render if it matches the active view ─
    if (msgThreadId !== currentThreadId) {
      if (data.type === 'done' && msgThreadId) {
        unreadSet[msgThreadId] = true;
        renderThreadList();
      }
      return;
    }

    if (data.type === 'thinking') {
      if (!thinkingRowEl) thinkingRowEl = appendThinkingRow();

    } else if (data.type === 'token') {
      removeThinkingRow();
      if (!currentAgentTextEl) {
        var agentRow = document.createElement('div');
        agentRow.className = 'msg-row agent-row';
        agentRow.innerHTML =
          '<div class="msg-avatar agent-av">AI</div>' +
          '<div class="msg-col">' +
            '<div class="msg-header"><div class="msg-name">CDCN Agent</div><div class="msg-time"></div></div>' +
            '<div class="msg-text"></div>' +
          '</div>';
        chatBody.appendChild(agentRow);
        currentAgentTextEl = agentRow.querySelector('.msg-text');
        currentAgentTimeEl = agentRow.querySelector('.msg-time');
      }
      currentAgentRaw += data.content;
      currentAgentTextEl.innerHTML = renderMarkdown(currentAgentRaw);
      addCopyButtons(currentAgentTextEl);
      messagesEl.scrollTop = messagesEl.scrollHeight;

    } else if (data.type === 'done') {
      removeThinkingRow();
      if (currentAgentTextEl) {
        currentAgentTextEl.innerHTML = renderMarkdown(currentAgentRaw);
        addCopyButtons(currentAgentTextEl);
      }
      if (currentAgentTimeEl) {
        currentAgentTimeEl.textContent = fmtTime(new Date());
      }
      currentAgentTextEl = null;
      currentAgentTimeEl = null;
      currentAgentRaw    = '';
      sendBtn.disabled = false;
      input.disabled = false;
      input.focus();

    } else if (data.type === 'error') {
      removeThinkingRow();
      appendSystem('Error: ' + (data.detail || 'unknown'));
      sendBtn.disabled = false;
      input.disabled = false;
    }
  }

  function appendAgentRow(ts) {
    var timeStr = fmtTime(ts);
    var row = document.createElement('div');
    row.className = 'msg-row agent-row';
    row.innerHTML =
      '<div class="msg-avatar agent-av">AI</div>' +
      '<div class="msg-col">' +
        '<div class="msg-header"><div class="msg-name">CDCN Agent</div>' +
          (timeStr ? '<div class="msg-time">' + timeStr + '</div>' : '') +
        '</div>' +
        '<div class="msg-text"></div>' +
      '</div>';
    chatBody.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    return row.querySelector('.msg-text');
  }

  function appendUserRow(text, ts, displayName, isMe) {
    if (typeof displayName === 'undefined') displayName = '';
    if (typeof isMe === 'undefined') isMe = true;
    var timeStr = fmtTime(ts);
    var label = isMe ? 'You' : (displayName || 'User');
    var initials = isMe ? 'You' : (displayName ? displayName.charAt(0).toUpperCase() : 'U');
    var rowClass = isMe ? 'msg-row user-row' : 'msg-row user-row other-user-row';
    var avClass = isMe ? 'msg-avatar user-av' : 'msg-avatar other-user-av';
    var row = document.createElement('div');
    row.className = rowClass;
    row.innerHTML =
      '<div class="' + avClass + '">' + escapeHtml(initials) + '</div>' +
      '<div class="msg-col">' +
        '<div class="msg-header"><div class="msg-name">' + escapeHtml(label) + '</div>' +
          (timeStr ? '<div class="msg-time">' + timeStr + '</div>' : '') +
        '</div>' +
        '<div class="msg-text"><div class="user-bubble">' + escapeHtml(text) + '</div></div>' +
      '</div>';
    chatBody.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function appendSystem(text) {
    var row = document.createElement('div');
    row.className = 'msg-row system-row';
    row.innerHTML = '<span class="system-text">' + escapeHtml(text) + '</span>';
    chatBody.appendChild(row);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  // ── Send ──────────────────────────────────────────────────────────────────
  function sendMessage() {
    var text = input.value.trim();
    if (!text || ws.readyState !== WebSocket.OPEN) return;
    appendUserRow(text, new Date());
    var payload = { message: text };
    if (currentThreadId) payload.thread_id = currentThreadId;
    ws.send(JSON.stringify(payload));
    input.value = '';
    input.style.height = '';
    sendBtn.disabled = true;
    input.disabled = true;
  }

  sendBtn.addEventListener('click', sendMessage);
  input.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  });
  input.addEventListener('input', function () {
    this.style.height = '';
    this.style.height = Math.min(this.scrollHeight, 160) + 'px';
  });

  // ── Autocomplete helper ───────────────────────────────────────────────────
  function setupAutocomplete(inputEl, dropdownEl, onSelect, excludeList) {
    var debounceTimer = null;
    var focusedIdx = -1;

    inputEl.addEventListener('input', function () {
      clearTimeout(debounceTimer);
      var q = inputEl.value.trim();
      if (q.length < 1) { dropdownEl.classList.remove('open'); return; }
      debounceTimer = setTimeout(function () {
        fetch('/api/users/search?q=' + encodeURIComponent(q))
          .then(function(r) { return r.ok ? r.json() : null; })
          .then(function(data) {
            if (!data || !data.users) { dropdownEl.classList.remove('open'); return; }
            var excl = excludeList ? excludeList() : [];
            var filtered = data.users.filter(function(u) { return excl.indexOf(u) === -1; });
            if (filtered.length === 0) {
              dropdownEl.innerHTML = '<div class="ac-no-results">No matches</div>';
              dropdownEl.classList.add('open');
              return;
            }
            focusedIdx = -1;
            dropdownEl.innerHTML = '';
            for (var i = 0; i < filtered.length; i++) {
              (function(uname, idx) {
                var opt = document.createElement('div');
                opt.className = 'ac-option';
                opt.textContent = uname;
                opt.onmousedown = function(e) { e.preventDefault(); };
                opt.onclick = function() {
                  onSelect(uname);
                  inputEl.value = '';
                  dropdownEl.classList.remove('open');
                };
                dropdownEl.appendChild(opt);
              })(filtered[i], i);
            }
            dropdownEl.classList.add('open');
          })
          .catch(function() { dropdownEl.classList.remove('open'); });
      }, 200);
    });

    inputEl.addEventListener('keydown', function(e) {
      var options = dropdownEl.querySelectorAll('.ac-option');
      if (!dropdownEl.classList.contains('open') || options.length === 0) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        focusedIdx = Math.min(focusedIdx + 1, options.length - 1);
        updateFocus(options);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        focusedIdx = Math.max(focusedIdx - 1, 0);
        updateFocus(options);
      } else if (e.key === 'Enter' && focusedIdx >= 0) {
        e.preventDefault();
        options[focusedIdx].click();
      } else if (e.key === 'Escape') {
        dropdownEl.classList.remove('open');
      }
    });

    inputEl.addEventListener('blur', function() {
      setTimeout(function() { dropdownEl.classList.remove('open'); }, 150);
    });

    function updateFocus(options) {
      for (var i = 0; i < options.length; i++) {
        options[i].classList.toggle('focused', i === focusedIdx);
      }
    }
  }

  // ── New Thread Modal ──────────────────────────────────────────────────────
  var newThreadParticipants = [];
  var newThreadChips = document.getElementById('new-thread-chips');
  var newThreadInput = document.getElementById('new-thread-user-input');
  var newThreadAc    = document.getElementById('new-thread-ac');
  var newThreadName  = document.getElementById('new-thread-name');

  setupAutocomplete(newThreadInput, newThreadAc, function(uname) {
    if (newThreadParticipants.indexOf(uname) === -1) {
      newThreadParticipants.push(uname);
      renderNewThreadChips();
    }
  }, function() { return newThreadParticipants; });

  function renderNewThreadChips() {
    newThreadChips.innerHTML = '';
    for (var i = 0; i < newThreadParticipants.length; i++) {
      (function(uname) {
        var chip = document.createElement('span');
        var isCreator = uname === myUsername;
        chip.className = 'participant-chip' + (isCreator ? ' creator' : '');
        if (isCreator) {
          chip.innerHTML = escapeHtml(uname) + ' <span style="font-size:.65rem;color:var(--text-muted);">(you)</span>';
        } else {
          chip.innerHTML = escapeHtml(uname) + ' <button class="chip-remove" title="Remove">&times;</button>';
          chip.querySelector('.chip-remove').onclick = function() {
            newThreadParticipants = newThreadParticipants.filter(function(u) { return u !== uname; });
            renderNewThreadChips();
          };
        }
        newThreadChips.appendChild(chip);
      })(newThreadParticipants[i]);
    }
  }

  window.openNewThreadModal = function () {
    // Auto-add the current user as first participant (non-removable)
    newThreadParticipants = [myUsername];
    newThreadName.value = '';
    newThreadInput.value = '';
    renderNewThreadChips();
    // Show/hide participant input based on permissions
    var participantGroup = newThreadInput.closest('.form-group');
    if (participantGroup) participantGroup.style.display = canGroup ? '' : 'none';
    document.getElementById('new-thread-modal').classList.add('open');
    setTimeout(function() { newThreadName.focus(); }, 100);
  };

  window.closeNewThreadModal = function () {
    document.getElementById('new-thread-modal').classList.remove('open');
  };

  window.createThread = function () {
    var name = newThreadName.value.trim();
    if (!name) { newThreadName.focus(); return; }
    var isGroup = newThreadParticipants.length > 1;
    if (isGroup && !canGroup) { alert('You do not have permission to create group threads.'); return; }
    if (!isGroup && !canIndividual) { alert('You do not have permission to create individual threads.'); return; }
    var btn = document.getElementById('create-thread-btn');
    btn.disabled = true;
    fetch('/api/threads', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name, participants: newThreadParticipants })
    })
      .then(function(r) {
        if (r.ok) return r.json();
        return r.json().then(function(err) { alert(err.detail || 'Failed'); return null; });
      })
      .then(function(data) {
        btn.disabled = false;
        if (!data) return;
        window.closeNewThreadModal();
        var selectIsGroup = newThreadParticipants.length > 1;
        loadThreads(function() {
          selectThread(data.id, data.name, selectIsGroup);
        });
      })
      .catch(function() { btn.disabled = false; });
  };

  // ── Manage Participants Modal ─────────────────────────────────────────────
  var participantsChips = document.getElementById('participants-chips');
  var participantsInput = document.getElementById('participants-user-input');
  var participantsAc    = document.getElementById('participants-ac');
  var currentParticipants = [];
  var threadCreator = '';

  setupAutocomplete(participantsInput, participantsAc, function(uname) {
    if (currentParticipants.indexOf(uname) !== -1) return;
    fetch('/api/threads/' + currentThreadId + '/participants', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: uname })
    })
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (data) {
          currentParticipants.push(uname);
          renderParticipantsChips();
          loadThreads();
        }
      })
      .catch(function() {});
  }, function() { return currentParticipants; });

  function renderParticipantsChips() {
    participantsChips.innerHTML = '';
    for (var i = 0; i < currentParticipants.length; i++) {
      (function(uname) {
        var chip = document.createElement('span');
        var isCreator = uname === threadCreator;
        chip.className = 'participant-chip' + (isCreator ? ' creator' : '');
        if (isCreator) {
          chip.innerHTML = escapeHtml(uname) + ' <span style="font-size:.65rem;color:var(--text-muted);">(creator)</span>';
        } else {
          chip.innerHTML = escapeHtml(uname) + ' <button class="chip-remove" title="Remove">&times;</button>';
          chip.querySelector('.chip-remove').onclick = function() {
            fetch('/api/threads/' + currentThreadId + '/participants/' + encodeURIComponent(uname), { method: 'DELETE' })
              .then(function(r) { return r.ok ? r.json() : null; })
              .then(function(data) {
                if (data && data.removed) {
                  currentParticipants = currentParticipants.filter(function(u) { return u !== uname; });
                  renderParticipantsChips();
                  loadThreads();
                }
              })
              .catch(function() {});
          };
        }
        participantsChips.appendChild(chip);
      })(currentParticipants[i]);
    }
  }

  window.openParticipantsModal = function () {
    if (!currentThreadId) return;
    participantsInput.value = '';
    participantsChips.innerHTML = '<span style="font-size:.78rem;color:var(--text-muted);">Loading...</span>';
    document.getElementById('participants-modal').classList.add('open');
    fetch('/api/threads/' + currentThreadId)
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) {
        if (!data) return;
        currentParticipants = data.participants || [];
        threadCreator = data.created_by || '';
        renderParticipantsChips();
      })
      .catch(function() {});
  };

  window.closeParticipantsModal = function () {
    document.getElementById('participants-modal').classList.remove('open');
  };

  // ── Keyboard accessibility ─────────────────────────────────────────────────

  // Escape closes modals
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      var newThreadModal = document.getElementById('new-thread-modal');
      var participantsModal = document.getElementById('participants-modal');
      if (newThreadModal && newThreadModal.style.display !== 'none' && typeof closeNewThreadModal === 'function') {
        closeNewThreadModal();
      } else if (participantsModal && participantsModal.style.display !== 'none' && typeof closeParticipantsModal === 'function') {
        closeParticipantsModal();
      }
    }
  });

  // Arrow keys navigate thread list
  document.addEventListener('keydown', function(e) {
    var body = document.getElementById('thread-list-body');
    if (!body || document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA') return;
    var items = body.querySelectorAll('.thread-item');
    if (!items.length) return;
    var idx = Array.from(items).indexOf(document.activeElement);
    if (e.key === 'ArrowDown') { e.preventDefault(); items[Math.min(idx + 1, items.length - 1)].focus(); }
    if (e.key === 'ArrowUp') { e.preventDefault(); items[Math.max(idx - 1, 0)].focus(); }
    if (e.key === 'Enter' && idx >= 0) { e.preventDefault(); items[idx].click(); }
  });

  // ── Initialise ─────────────────────────────────────────────────────────────
  connectWs();
  loadThreads(function() {
    selectGeneral();
  });

}());
