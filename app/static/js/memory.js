
(function(){
  const searchInput = document.getElementById('memory-search');
  const searchBtn = document.getElementById('memory-search-btn');
  const resultsDiv = document.getElementById('memory-results');
  const sessionsDiv = document.getElementById('memory-sessions');
  const daysSelect = document.getElementById('memory-days');

  function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

  async function loadSessions() {
    const days = daysSelect.value || 7;
    sessionsDiv.innerHTML = '<p style="color:var(--text-subtle)">Loading sessions&hellip;</p>';
    try {
      const r = await fetch('/api/memory/sessions?days=' + days);
      const data = await r.json();
      if (!data.sessions || data.sessions.length === 0) {
        sessionsDiv.innerHTML = '<p style="color:var(--text-subtle)">No sessions found.</p>';
        return;
      }
      let html = '<table class="memory-table"><thead><tr><th>Date</th><th>Session</th><th>User</th><th>Messages</th><th></th></tr></thead><tbody>';
      data.sessions.forEach(s => {
        html += '<tr><td>' + esc(s.date) + '</td><td><code>' + esc(s.session_id) + '</code></td>'
          + '<td>' + esc(s.user || '-') + '</td><td>' + s.messages + '</td>'
          + '<td><button class="memory-view-btn" onclick="viewSession(\\'' + esc(s.session_id) + '\\',\\'' + esc(s.date) + '\\')">View</button></td></tr>';
      });
      html += '</tbody></table>';
      sessionsDiv.innerHTML = html;
    } catch(e) { sessionsDiv.innerHTML = '<p style="color:#e74c3c">Error loading sessions.</p>'; }
  }

  window.viewSession = async function(sid, date) {
    resultsDiv.innerHTML = '<p style="color:var(--text-subtle)">Loading session&hellip;</p>';
    try {
      const r = await fetch('/api/memory/session?session_id=' + encodeURIComponent(sid) + '&date=' + encodeURIComponent(date));
      const data = await r.json();
      if (!data.messages || data.messages.length === 0) {
        resultsDiv.innerHTML = '<p style="color:var(--text-subtle)">No messages in this session.</p>';
        return;
      }
      let html = '<h4>Session ' + esc(sid) + ' &mdash; ' + esc(date) + '</h4><div class="memory-transcript">';
      data.messages.forEach(m => {
        const cls = m.role === 'user' ? 'memory-msg-user' : 'memory-msg-assistant';
        html += '<div class="' + cls + '"><strong>' + esc(m.role) + ':</strong> ' + esc(m.content) + '</div>';
      });
      html += '</div>';
      resultsDiv.innerHTML = html;
    } catch(e) { resultsDiv.innerHTML = '<p style="color:#e74c3c">Error loading session.</p>'; }
  };

  async function doSearch() {
    const q = searchInput.value.trim();
    if (!q) return;
    resultsDiv.innerHTML = '<p style="color:var(--text-subtle)">Searching&hellip;</p>';
    try {
      const r = await fetch('/api/memory/search?q=' + encodeURIComponent(q));
      const data = await r.json();
      if (!data.matches || data.matches.length === 0) {
        resultsDiv.innerHTML = '<p style="color:var(--text-subtle)">No results for &ldquo;' + esc(q) + '&rdquo;.</p>';
        return;
      }
      let html = '<h4>' + data.matches.length + ' result(s) for &ldquo;' + esc(q) + '&rdquo;</h4>';
      data.matches.forEach(m => {
        html += '<div class="memory-search-result">'
          + '<div class="memory-result-meta">' + esc(m.ts || '') + ' &mdash; ' + esc(m.display_name || m.username || '') + ' (' + esc(m.role || '') + ')</div>'
          + '<div class="memory-result-excerpt">' + (m.excerpt || esc(m.content || '').substring(0,200)) + '</div>'
          + '</div>';
      });
      resultsDiv.innerHTML = html;
    } catch(e) { resultsDiv.innerHTML = '<p style="color:#e74c3c">Search failed.</p>'; }
  }

  searchBtn.addEventListener('click', doSearch);
  searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });
  daysSelect.addEventListener('change', loadSessions);
  loadSessions();
})();
