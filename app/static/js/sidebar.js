
/* ── All functions here are at module scope (global) ─────────────────── */

var moonIcon = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
var sunIcon  = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';

function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('overlay').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('overlay').classList.remove('open');
}

function toggleTheme() {
  var current = document.documentElement.getAttribute('data-theme') || 'light';
  var next = current === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  var btn = document.getElementById('theme-toggle');
  if (btn) btn.innerHTML = next === 'dark' ? sunIcon : moonIcon;
}

function _initThemeBtn() {
  var btn = document.getElementById('theme-toggle');
  if (!btn) return;
  var theme = document.documentElement.getAttribute('data-theme') || 'light';
  btn.innerHTML = theme === 'dark' ? sunIcon : moonIcon;
}

function pollStatus() {
  fetch('/api/status')
    .then(function(r) { return r.json(); })
    .then(function(s) {
      var mode = (s.mode || 'unknown');
      var lbl  = mode.replace(/_/g, ' ');
      var dot = document.getElementById('status-dot');
      var label = document.getElementById('status-label');
      if (dot) { dot.className = 'status-dot ' + mode; }
      if (label) { label.textContent = lbl; }
    })
    .catch(function() {});
}

function sendHeartbeat() {
  fetch('/api/heartbeat', { method: 'POST' }).catch(function() {});
}

function pollOnlineUsers() {
  fetch('/api/online')
    .then(function(r) { return r.json(); })
    .then(function(d) {
      var list = document.getElementById('online-users-list');
      if (!list) return;
      var users = d.users || [];
      if (users.length === 0) {
        list.innerHTML = '<span style="font-size:.72rem;color:var(--text-subtle);padding:.15rem .55rem;">No one online</span>';
        return;
      }
      list.innerHTML = users.map(function(u) {
        var init = (u.username || '?')[0].toUpperCase();
        return '<div class="online-user-chip" title="' + u.username + '">' +
          '<span class="online-dot"></span>' +
          '<span class="online-avatar">' + init + '</span>' +
          '<span class="online-name">' + u.username + '</span>' +
          '</div>';
      }).join('');
    })
    .catch(function() {});
}

/* ── Boot: apply theme, start polls ─────────────────────────────────── */
(function() {
  var saved = localStorage.getItem('theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  _initThemeBtn();
  sendHeartbeat();
  pollStatus();
  pollOnlineUsers();
  setInterval(sendHeartbeat, 60000);
  setInterval(pollStatus, 30000);
  setInterval(pollOnlineUsers, 30000);
})();
