function _post(url, body) {
  return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}) })
    .then(function(r) { return r.json(); });
}

function adminAction(action, username) {
  if (!confirm(action + ' user "' + username + '"?')) return;
  _post('/api/admin/users/' + encodeURIComponent(username) + '/' + action)
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

function changeRole(username) {
  var sel = document.querySelector('.role-select[data-user="' + username + '"]');
  if (!sel) return;
  _post('/api/admin/users/' + encodeURIComponent(username) + '/role', { role: sel.value })
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

// ── Edit user modal ──
function openEditUser(username, displayName, email) {
  document.getElementById('edit-username').value = username;
  document.getElementById('edit-display-name').value = displayName;
  document.getElementById('edit-email').value = email;
  document.getElementById('edit-password').value = '';
  document.getElementById('edit-modal-title').textContent = 'Edit ' + username;
  document.getElementById('edit-modal').style.display = 'flex';
}

function closeEditModal() {
  document.getElementById('edit-modal').style.display = 'none';
}

function saveUserInfo() {
  var username = document.getElementById('edit-username').value;
  var data = {
    display_name: document.getElementById('edit-display-name').value.trim(),
    email: document.getElementById('edit-email').value.trim()
  };
  var pw = document.getElementById('edit-password').value;
  if (pw) data.password = pw;
  _post('/api/admin/users/' + encodeURIComponent(username) + '/edit', data)
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

// ── Registrations ──
function approveReg(id) {
  _post('/api/admin/registrations/' + id + '/approve')
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

function rejectReg(id) {
  var reason = prompt('Reason for rejection (optional):') || '';
  _post('/api/admin/registrations/' + id + '/reject', { reason: reason })
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

// ── Create user ──
document.getElementById('create-user-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var data = {
    username: document.getElementById('new-username').value.trim(),
    password: document.getElementById('new-password').value,
    role:     document.getElementById('new-role').value,
  };
  if (!data.username || !data.password) return;
  _post('/api/admin/users', data)
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed to create user'); })
    .catch(function() { alert('Request failed'); });
});

// ── Roles ──
function saveRole(name) {
  var cbs = document.querySelectorAll('.role-perm-cb[data-role="' + name + '"]');
  var perms = [];
  cbs.forEach(function(cb) { if (cb.checked) perms.push(cb.value); });
  var descEl = document.querySelector('.role-desc-input[data-role="' + name + '"]');
  var desc = descEl ? descEl.value.trim() : '';
  _post('/api/admin/roles/' + encodeURIComponent(name), { permissions: perms, description: desc })
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

function deleteRole(name) {
  if (!confirm('Delete role "' + name + '"? Users with this role will need reassignment.')) return;
  fetch('/api/admin/roles/' + encodeURIComponent(name), { method: 'DELETE' })
    .then(function(r) { return r.json(); })
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
}

document.getElementById('add-role-form').addEventListener('submit', function(e) {
  e.preventDefault();
  var name = document.getElementById('new-role-name').value.trim().toLowerCase();
  var desc = document.getElementById('new-role-desc').value.trim();
  if (!name) return;
  _post('/api/admin/roles', { name: name, description: desc, permissions: [] })
    .then(function(d) { if (d.ok) location.reload(); else alert(d.detail || 'Failed'); })
    .catch(function() { alert('Request failed'); });
});
