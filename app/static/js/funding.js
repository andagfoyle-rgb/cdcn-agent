
function escapeHtml(s) {
  var d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function badgeHtml(cls, label) {
  return '<span class="funding-badge ' + cls + '">' + escapeHtml(label) + '</span>';
}

function renderCard(o) {
  var titleInner = o.link
    ? '<a href="' + escapeHtml(o.link) + '" target="_blank" rel="noopener">' + escapeHtml(o.title) + '</a>'
    : escapeHtml(o.title);
  var summary = o.summary || '';
  if (summary.length > 200) summary = summary.substring(0, 197) + '...';
  var meta = badgeHtml(o.relevance, o.relevance);
  if (o.deadline) meta += badgeHtml('deadline', 'Deadline: ' + o.deadline);
  if (o.amount)   meta += badgeHtml('amount', o.amount);
  return '<div class="funding-card">' +
    '<div class="funding-card-title">' + titleInner + '</div>' +
    '<div class="funding-card-funder">' + escapeHtml(o.funder) + '</div>' +
    (summary ? '<div class="funding-card-summary">' + escapeHtml(summary) + '</div>' : '') +
    '<div class="funding-card-meta">' + meta + '</div>' +
    '</div>';
}

function renderStats(opps) {
  var high = 0, med = 0, low = 0, withDeadline = 0;
  var funders = {};
  opps.forEach(function(o) {
    if (o.relevance === 'high') high++;
    else if (o.relevance === 'medium') med++;
    else low++;
    if (o.deadline) withDeadline++;
    funders[o.funder] = 1;
  });
  function stat(val, label) {
    return '<div class="funding-stat"><div class="funding-stat-value">' + val + '</div><div class="funding-stat-label">' + label + '</div></div>';
  }
  return stat(opps.length, 'Total') + stat(high, 'High Relevance') + stat(med, 'Medium') + stat(Object.keys(funders).length, 'Funders') + stat(withDeadline, 'With Deadlines');
}

function renderReport(opps) {
  var high = opps.filter(function(o) { return o.relevance === 'high'; });
  var med  = opps.filter(function(o) { return o.relevance === 'medium'; });
  var low  = opps.filter(function(o) { return o.relevance === 'low'; });
  var html = '';
  if (high.length) {
    html += '<div class="funding-section"><div class="funding-section-title">' +
      '<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="5" fill="#22c55e"/></svg>' +
      'High Relevance (' + high.length + ')</div>';
    high.forEach(function(o) { html += renderCard(o); });
    html += '</div>';
  }
  if (med.length) {
    html += '<div class="funding-section"><div class="funding-section-title">' +
      '<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="5" fill="#eab308"/></svg>' +
      'Medium Relevance (' + med.length + ')</div>';
    med.forEach(function(o) { html += renderCard(o); });
    html += '</div>';
  }
  if (low.length) {
    html += '<div class="funding-section"><div class="funding-section-title">' +
      '<svg width="10" height="10" viewBox="0 0 10 10"><circle cx="5" cy="5" r="5" fill="#a1a1aa"/></svg>' +
      'Low Relevance (' + low.length + ')</div>';
    low.forEach(function(o) { html += renderCard(o); });
    html += '</div>';
  }
  return html;
}

function loadFunding() {
  fetch('/api/funding/opportunities')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var opps = data.opportunities || [];
      document.getElementById('funding-stats').innerHTML = renderStats(opps);
      if (opps.length === 0) {
        document.getElementById('funding-report').innerHTML =
          '<div class="empty-state">' +
          '<svg width="40" height="40" fill="none" stroke="currentColor" stroke-width="1.5" viewBox="0 0 24 24"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>' +
          '<p>No funding opportunities found in the last 30 days.</p>' +
          '<p style="font-size:.78rem;margin-top:.4rem;color:var(--text-subtle);">Click Refresh Feeds to scan for new opportunities.</p>' +
          '</div>';
      } else {
        document.getElementById('funding-report').innerHTML = renderReport(opps);
      }
      document.getElementById('funding-meta').textContent =
        opps.length + ' opportunity/ies found (last 30 days). Last refreshed: ' + new Date().toLocaleString();
    })
    .catch(function(err) {
      document.getElementById('funding-report').innerHTML =
        '<div class="alert alert-danger">Failed to load funding data: ' + err + '</div>';
    });
}

function refreshFeeds() {
  var btn = document.getElementById('refresh-btn');
  btn.disabled = true;
  btn.classList.add('funding-refresh-spin');
  btn.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Refreshing...';
  fetch('/api/funding/refresh', { method: 'POST' })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      btn.disabled = false;
      btn.classList.remove('funding-refresh-spin');
      btn.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Refresh Feeds';
      loadFunding();
    })
    .catch(function(err) {
      btn.disabled = false;
      btn.classList.remove('funding-refresh-spin');
      btn.innerHTML = '<svg width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 24 24"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg> Refresh Feeds';
      document.getElementById('funding-meta').textContent = 'Refresh failed: ' + err;
    });
}

loadFunding();
