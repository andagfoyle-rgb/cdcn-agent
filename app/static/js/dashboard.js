(function() {
  'use strict';

  var periodSelect = document.getElementById('period-select');
  var tokenChart = null, usersChart = null, skillsChart = null;
  var allUserData = [];
  var sortCol = 4, sortAsc = false;

  // ── Colour helpers ────────────────────────────────────────────────
  var isDark = document.documentElement.getAttribute('data-theme') === 'dark';
  var gridColor = isDark ? 'rgba(255,255,255,.1)' : 'rgba(0,0,0,.08)';
  var textColor = isDark ? '#b0b0b0' : '#666';
  var inputColor = 'rgba(99,102,241,.85)';
  var outputColor = 'rgba(244,114,182,.85)';
  var inputBg = 'rgba(99,102,241,.15)';
  var outputBg = 'rgba(244,114,182,.15)';

  Chart.defaults.color = textColor;
  Chart.defaults.borderColor = gridColor;
  Chart.defaults.font.family = "'Inter','system-ui',sans-serif";
  Chart.defaults.font.size = 11;

  function fmtNum(n) { return n == null ? '0' : n.toLocaleString(); }
  function fmtUsd(n) { return n == null ? '-' : '$' + n.toFixed(2); }

  // ── Summary cards ─────────────────────────────────────────────────
  function renderCards(data) {
    var t = data.totals;
    var c = data.cost || {};
    var cards = [
      { label: 'Total API Calls', value: fmtNum(t.call_count), color: '#6366f1' },
      { label: 'Input Tokens', value: fmtNum(t.prompt_tokens), color: '#6366f1' },
      { label: 'Output Tokens', value: fmtNum(t.completion_tokens), color: '#ec4899' },
      { label: 'Total Tokens', value: fmtNum(t.total_tokens), color: '#8b5cf6' },
      { label: 'Period Cost', value: fmtUsd(c.total_cost), color: '#10b981' },
      { label: 'Projected Monthly', value: c.projected_monthly != null ? fmtUsd(c.projected_monthly) : 'N/A', color: '#f59e0b' },
    ];
    var html = '';
    for (var i = 0; i < cards.length; i++) {
      var cd = cards[i];
      html += '<div class="card" style="text-align:center;padding:1rem;">'
            + '<div style="font-size:.78rem;color:var(--text-muted);margin-bottom:.25rem;">' + cd.label + '</div>'
            + '<div style="font-size:1.5rem;font-weight:700;color:' + cd.color + ';">' + cd.value + '</div>'
            + '</div>';
    }
    // Cost breakdown subtitle
    if (c.input_cost != null) {
      html += '<div class="card" style="grid-column:1/-1;padding:.75rem 1rem;font-size:.82rem;color:var(--text-muted);">'
            + '<strong style="color:var(--text);">Cost breakdown:</strong> '
            + 'Input: $' + c.input_cost.toFixed(4) + ' &middot; Output: $' + c.output_cost.toFixed(4)
            + ' &middot; Model: ' + (c.pricing ? c.pricing.model : 'unknown')
            + ' ($' + (c.pricing ? c.pricing.input_per_m : '?') + '/$' + (c.pricing ? c.pricing.output_per_m : '?') + ' per 1M tokens)'
            + '</div>';
    }
    document.getElementById('summary-cards').innerHTML = html;
  }

  // ── Token timeline chart ──────────────────────────────────────────
  function renderTimeline(data) {
    var labels = data.timeline.map(function(r) {
      if (data.bucket_label === 'hour') {
        var parts = r.bucket.split('T');
        return parts.length > 1 ? parts[1] + ':00' : r.bucket;
      }
      return r.bucket.slice(5);
    });
    var promptData = data.timeline.map(function(r) { return r.prompt_tokens; });
    var compData = data.timeline.map(function(r) { return r.completion_tokens; });

    if (tokenChart) tokenChart.destroy();
    tokenChart = new Chart(document.getElementById('token-timeline-chart'), {
      type: 'line',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Input Tokens',
            data: promptData,
            borderColor: inputColor,
            backgroundColor: inputBg,
            fill: true,
            tension: .3,
            pointRadius: promptData.length > 60 ? 0 : 3,
          },
          {
            label: 'Output Tokens',
            data: compData,
            borderColor: outputColor,
            backgroundColor: outputBg,
            fill: true,
            tension: .3,
            pointRadius: compData.length > 60 ? 0 : 3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        scales: {
          x: { grid: { display: false }, ticks: { maxRotation: 45, autoSkip: true, maxTicksLimit: 20 } },
          y: { beginAtZero: true, ticks: { callback: function(v) { return v >= 1000 ? (v/1000).toFixed(0) + 'k' : v; } } },
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: function(ctx) { return ctx.dataset.label + ': ' + fmtNum(ctx.raw); }
            }
          }
        }
      }
    });
  }

  // ── Top users chart ───────────────────────────────────────────────
  function renderTopUsers(data) {
    var top = data.top_users.slice(0, 10);
    var labels = top.map(function(u) { return u.user; });
    var prompt = top.map(function(u) { return u.prompt_tokens; });
    var comp = top.map(function(u) { return u.completion_tokens; });

    if (usersChart) usersChart.destroy();
    usersChart = new Chart(document.getElementById('top-users-chart'), {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          { label: 'Input Tokens', data: prompt, backgroundColor: inputColor },
          { label: 'Output Tokens', data: comp, backgroundColor: outputColor },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        scales: {
          x: { stacked: true, ticks: { callback: function(v) { return v >= 1000 ? (v/1000).toFixed(0) + 'k' : v; } } },
          y: { stacked: true, grid: { display: false } },
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: function(ctx) { return ctx.dataset.label + ': ' + fmtNum(ctx.raw); }
            }
          }
        }
      }
    });
  }

  // ── Skill breakdown chart ─────────────────────────────────────────
  function renderSkills(data) {
    var sk = data.skills.filter(function(s) { return s.call_count > 0; });
    if (sk.length === 0) {
      document.getElementById('skills-chart').parentElement.style.display = 'none';
      return;
    }
    document.getElementById('skills-chart').parentElement.style.display = '';
    var labels = sk.map(function(s) { return s.skill || 'general'; });
    var calls = sk.map(function(s) { return s.call_count; });
    var colors = ['#6366f1','#ec4899','#8b5cf6','#f59e0b','#10b981','#ef4444','#06b6d4','#f97316','#84cc16','#a855f7'];

    if (skillsChart) skillsChart.destroy();
    skillsChart = new Chart(document.getElementById('skills-chart'), {
      type: 'doughnut',
      data: {
        labels: labels,
        datasets: [{ data: calls, backgroundColor: colors.slice(0, labels.length) }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'right', labels: { boxWidth: 12, padding: 10 } },
        }
      }
    });
  }

  // ── Activity lists ────────────────────────────────────────────────
  function renderActivity(data) {
    var authEl = document.getElementById('auth-events-list');
    var ae = data.auth_events || {};
    var authHtml = '';
    var aeKeys = Object.keys(ae);
    if (aeKeys.length === 0) authHtml = '<span style="color:var(--text-subtle);">No events</span>';
    else for (var i = 0; i < aeKeys.length; i++) authHtml += '<div style="display:flex;justify-content:space-between;padding:.2rem 0;border-bottom:1px solid var(--border);"><span>' + aeKeys[i] + '</span><strong>' + ae[aeKeys[i]] + '</strong></div>';
    authEl.innerHTML = authHtml;

    var docEl = document.getElementById('doc-ops-list');
    var dops = data.document_ops || {};
    var docHtml = '';
    var dopKeys = Object.keys(dops);
    if (dopKeys.length === 0) docHtml = '<span style="color:var(--text-subtle);">No operations</span>';
    else for (var i = 0; i < dopKeys.length; i++) docHtml += '<div style="display:flex;justify-content:space-between;padding:.2rem 0;border-bottom:1px solid var(--border);"><span>' + dopKeys[i] + '</span><strong>' + dops[dopKeys[i]] + '</strong></div>';
    docEl.innerHTML = docHtml;

    var dreamEl = document.getElementById('dream-cycles-info');
    var dc = data.dream_cycles || {};
    if (!dc.cycles) dreamEl.innerHTML = '<span style="color:var(--text-subtle);">No dream cycles</span>';
    else dreamEl.innerHTML = '<div style="display:flex;justify-content:space-between;padding:.2rem 0;"><span>Cycles</span><strong>' + (dc.cycles || 0) + '</strong></div>'
       + '<div style="display:flex;justify-content:space-between;padding:.2rem 0;"><span>Tasks completed</span><strong>' + (dc.completed || 0) + '</strong></div>'
       + '<div style="display:flex;justify-content:space-between;padding:.2rem 0;"><span>Tasks failed</span><strong>' + (dc.failed || 0) + '</strong></div>';
  }

  // ── User table ────────────────────────────────────────────────────
  function renderUserTable(data) {
    allUserData = data.users || [];
    sortAndRender();
  }

  function sortAndRender() {
    var fields = ['user','call_count','prompt_tokens','completion_tokens','_total','avg_latency_ms'];
    allUserData.sort(function(a, b) {
      var av, bv;
      if (sortCol === 4) { av = a.prompt_tokens + a.completion_tokens; bv = b.prompt_tokens + b.completion_tokens; }
      else { var f = fields[sortCol]; av = a[f]; bv = b[f]; }
      if (typeof av === 'string') return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortAsc ? av - bv : bv - av;
    });
    var html = '';
    for (var i = 0; i < allUserData.length; i++) {
      var u = allUserData[i];
      var total = u.prompt_tokens + u.completion_tokens;
      html += '<tr style="border-bottom:1px solid var(--border);">'
        + '<td style="padding:.4rem .5rem;">' + u.user + '</td>'
        + '<td style="padding:.4rem .5rem;text-align:right;">' + fmtNum(u.call_count) + '</td>'
        + '<td style="padding:.4rem .5rem;text-align:right;">' + fmtNum(u.prompt_tokens) + '</td>'
        + '<td style="padding:.4rem .5rem;text-align:right;">' + fmtNum(u.completion_tokens) + '</td>'
        + '<td style="padding:.4rem .5rem;text-align:right;font-weight:600;">' + fmtNum(total) + '</td>'
        + '<td style="padding:.4rem .5rem;text-align:right;">' + fmtNum(u.avg_latency_ms) + 'ms</td>'
        + '</tr>';
    }
    document.getElementById('user-table-body').innerHTML = html;
  }

  window.sortTable = function(col) {
    if (sortCol === col) sortAsc = !sortAsc;
    else { sortCol = col; sortAsc = col === 0; }
    sortAndRender();
  };

  window.toggleUserTable = function() {
    var wrap = document.getElementById('user-table-wrap');
    var btn = document.getElementById('toggle-table-btn');
    if (wrap.style.display === 'none') {
      wrap.style.display = '';
      btn.textContent = 'Hide Table';
    } else {
      wrap.style.display = 'none';
      btn.textContent = 'Show Table';
    }
  };

  // ── Load dashboard ────────────────────────────────────────────────
  window.loadDashboard = function() {
    var period = periodSelect.value;
    fetch('/api/admin/dashboard/stats?period=' + period)
      .then(function(r) { return r.json(); })
      .then(function(data) {
        renderCards(data);
        renderTimeline(data);
        renderTopUsers(data);
        renderSkills(data);
        renderActivity(data);
        renderUserTable(data);
      })
      .catch(function(err) {
        console.error('Dashboard load failed:', err);
      });
  };

  periodSelect.addEventListener('change', loadDashboard);
  loadDashboard();
})();
