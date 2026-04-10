
function renderActionItem(a) {
  var done = (a.status === 'completed' || a.status === 'closed');
  var isDeferred = (a.status === 'deferred');
  var isUrgent = (a.priority === 'high');
  var labelCls = 'ap-desc' + (done ? ' ap-done' : '');

  var who = a.assigned_to ? '<span class="ap-who">' + a.assigned_to + '</span>' : '';

  var priBadge = '';
  if (isUrgent) priBadge = '<span class="ap-badge ap-urgent">Urgent</span>';
  else if (a.priority === 'low') priBadge = '<span class="ap-badge ap-low">Low</span>';

  var statusBadge = '';
  if (done) statusBadge = '<span class="ap-badge ap-completed-badge">Completed</span>';
  else if (isDeferred) statusBadge = '<span class="ap-badge ap-deferred-badge">Deferred</span>';

  var meeting = a.meeting_date ? '<span class="ap-meta">Meeting: ' + a.meeting_date + '</span>' : '';
  var id = a.action_id || '';

  // Truncate description for preview, full text in expandable area
  var desc = a.description || '';
  var preview = desc.length > 90 ? desc.substring(0, 90) + '...' : desc;
  var hasMore = desc.length > 90;

  var expandHtml = '';
  if (hasMore) {
    expandHtml = '<div class="ap-full" id="full-' + id + '" style="display:none;">' + desc + '</div>' +
      '<a href="#" class="ap-expand" onclick="toggleExpand(\'' + id + '\', event)">Show more</a>';
  }

  // Status buttons — only show for non-completed items
  var btns = '';
  if (!done) {
    btns = '<div class="ap-buttons">';
    btns += '<button class="ap-btn ap-btn-complete" onclick="setStatus(\'' + id + '\',\'completed\')">Complete</button>';
    if (!isDeferred) {
      btns += '<button class="ap-btn ap-btn-defer" onclick="setStatus(\'' + id + '\',\'deferred\')">Defer</button>';
    } else {
      btns += '<button class="ap-btn ap-btn-reopen" onclick="setStatus(\'' + id + '\',\'open\')">Re-open</button>';
    }
    if (!isUrgent) {
      btns += '<button class="ap-btn ap-btn-urgent" onclick="setStatus(\'' + id + '\',\'open\',\'high\')">Urgent</button>';
    }
    btns += '</div>';
  }

  return '<div class="ap-card' + (done ? ' ap-card-done' : '') + (isUrgent && !done ? ' ap-card-urgent' : '') + '">' +
    '<div class="ap-header">' + who + priBadge + statusBadge + meeting + '</div>' +
    '<div class="' + labelCls + '">' + preview + '</div>' +
    expandHtml + btns + '</div>';
}

function toggleExpand(id, e) {
  e.preventDefault();
  var el = document.getElementById('full-' + id);
  var link = e.target;
  if (el.style.display === 'none') {
    el.style.display = 'block';
    link.textContent = 'Show less';
  } else {
    el.style.display = 'none';
    link.textContent = 'Show more';
  }
}

function setStatus(actionId, status, priority) {
  var body = { status: status };
  if (priority) body.priority = priority;
  fetch('/api/action-points/' + actionId + '/status', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  })
    .then(function(r) { return r.json(); })
    .then(function(data) { if (data.success) loadActionPoints(); })
    .catch(function(err) { console.error(err); });
}

function loadActionPoints() {
  fetch('/api/action-points')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var items = data.action_points || [];
      var open = items.filter(function(a) { return a.status === 'open' || a.status === 'in_progress'; });
      var done = items.filter(function(a) { return a.status === 'completed' || a.status === 'closed'; });
      var deferred = items.filter(function(a) { return a.status === 'deferred'; });
      var urgent = open.filter(function(a) { return a.priority === 'high'; });

      document.getElementById('ap-stats').innerHTML =
        '<div class="funding-stat"><div class="funding-stat-value">' + open.length + '</div><div class="funding-stat-label">Open</div></div>' +
        '<div class="funding-stat"><div class="funding-stat-value">' + urgent.length + '</div><div class="funding-stat-label">Urgent</div></div>' +
        '<div class="funding-stat"><div class="funding-stat-value">' + deferred.length + '</div><div class="funding-stat-label">Deferred</div></div>' +
        '<div class="funding-stat"><div class="funding-stat-value">' + done.length + '</div><div class="funding-stat-label">Completed</div></div>';

      var html = '';
      if (items.length === 0) {
        html = '<div class="empty-state">' +
          '<svg width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg>' +
          '<p>No action points yet.</p></div>';
      } else {
        if (open.length) {
          html += '<div class="funding-section"><div class="funding-section-title">Open (' + open.length + ')</div>';
          open.forEach(function(a) { html += renderActionItem(a); });
          html += '</div>';
        }
        if (deferred.length) {
          html += '<div class="funding-section"><div class="funding-section-title">Deferred (' + deferred.length + ')</div>';
          deferred.forEach(function(a) { html += renderActionItem(a); });
          html += '</div>';
        }
        if (done.length) {
          html += '<div class="funding-section"><div class="funding-section-title">Completed (' + done.length + ')</div>';
          done.forEach(function(a) { html += renderActionItem(a); });
          html += '</div>';
        }
      }
      document.getElementById('ap-list').innerHTML = html;
    })
    .catch(function(err) {
      document.getElementById('ap-list').innerHTML =
        '<div class="alert alert-danger">Failed to load action points: ' + err + '</div>';
    });
}

loadActionPoints();
