var calYear, calMonth, calEvents=[], canEdit=false;
var MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
var CAT_COLORS = {
  event:'#4f46e5', meeting:'#0891b2', funding:'#059669',
  statutory:'#dc2626', policy_review:'#d97706', contractual:'#7c3aed', other:'#6b7280'
};

function calInit(editPermission) {
  canEdit = editPermission;
  var d = new Date();
  calYear = d.getFullYear();
  calMonth = d.getMonth() + 1;
  loadMonth();
}

function calNav(delta) {
  calMonth += delta;
  if (calMonth < 1) { calMonth = 12; calYear--; }
  if (calMonth > 12) { calMonth = 1; calYear++; }
  loadMonth();
}

function calToday() {
  var d = new Date();
  calYear = d.getFullYear();
  calMonth = d.getMonth() + 1;
  loadMonth();
}

function loadMonth() {
  document.getElementById('cal-title').textContent = MONTHS[calMonth-1] + ' ' + calYear;
  fetch('/api/calendar/month?year=' + calYear + '&month=' + calMonth)
    .then(r => r.json())
    .then(data => {
      calEvents = data.events || [];
      renderGrid();
      renderUpcoming();
    })
    .catch(() => { calEvents = []; renderGrid(); });
}

function renderGrid() {
  var grid = document.getElementById('cal-grid');
  var today = new Date();
  var todayStr = today.getFullYear()+'-'+String(today.getMonth()+1).padStart(2,'0')+'-'+String(today.getDate()).padStart(2,'0');

  // Header
  var html = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d =>
    '<div style="text-align:center;font-size:.75rem;font-weight:600;color:var(--text-muted);padding:.5rem 0;">'+d+'</div>'
  ).join('');

  // Build weeks
  var first = new Date(calYear, calMonth-1, 1);
  var startDay = (first.getDay() + 6) % 7; // Monday=0
  var daysInMonth = new Date(calYear, calMonth, 0).getDate();

  // Group events by day
  var byDay = {};
  calEvents.forEach(function(ev) {
    var d = parseInt(ev.due_date.substring(8, 10), 10);
    if (!byDay[d]) byDay[d] = [];
    byDay[d].push(ev);
  });

  // Empty cells before first day
  for (var i = 0; i < startDay; i++) {
    html += '<div style="padding:.5rem;min-height:80px;border:1px solid var(--border);background:var(--surface-2);opacity:.3;border-radius:var(--radius-sm);"></div>';
  }

  for (var day = 1; day <= daysInMonth; day++) {
    var dateStr = calYear+'-'+String(calMonth).padStart(2,'0')+'-'+String(day).padStart(2,'0');
    var isToday = dateStr === todayStr;
    var border = isToday ? 'var(--primary)' : 'var(--border)';
    var bg = isToday ? 'var(--primary-subtle)' : 'var(--surface)';

    html += '<div style="padding:.5rem;min-height:80px;border:1px solid '+border+';background:'+bg+';border-radius:var(--radius-sm);cursor:pointer;transition:all var(--transition);" '
          + 'onmouseover="this.style.borderColor=\'var(--primary)\'" onmouseout="this.style.borderColor=\''+(isToday?'var(--primary)':'var(--border)')+'\'"'
          + (canEdit ? ' ondblclick="openAddModal(\''+dateStr+'\')"' : '') + '>';

    if (isToday) {
      html += '<span style="background:var(--primary);color:#fff;font-size:.75rem;font-weight:700;padding:.12rem .4rem;border-radius:20px;display:inline-block;">'+day+'</span>';
    } else {
      html += '<span style="font-size:.78rem;color:var(--text-muted);font-weight:500;">'+day+'</span>';
    }

    var dayEvents = byDay[day] || [];
    dayEvents.slice(0, 3).forEach(function(ev) {
      var col = CAT_COLORS[ev.category] || '#6b7280';
      var time = ev.event_time ? '<span style="opacity:.7;">'+ev.event_time.substring(0,5)+' </span>' : '';
      html += '<div onclick="showDetail('+ev.id+');event.stopPropagation();" '
            + 'style="margin-top:3px;padding:2px 5px;background:'+col+'18;border-left:2px solid '+col+';border-radius:3px;font-size:.68rem;color:var(--text);cursor:pointer;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">'
            + time + ev.title + '</div>';
    });
    if (dayEvents.length > 3) {
      html += '<div style="font-size:.62rem;color:var(--text-subtle);margin-top:2px;">+' + (dayEvents.length-3) + ' more</div>';
    }
    html += '</div>';
  }

  // Trailing empty cells
  var totalCells = startDay + daysInMonth;
  var remainder = totalCells % 7;
  if (remainder > 0) {
    for (var i = 0; i < 7 - remainder; i++) {
      html += '<div style="padding:.5rem;min-height:80px;border:1px solid var(--border);background:var(--surface-2);opacity:.3;border-radius:var(--radius-sm);"></div>';
    }
  }

  grid.innerHTML = html;
}

function renderUpcoming() {
  var bar = document.getElementById('upcoming-bar');
  var today = new Date();
  var upcoming = calEvents.filter(function(ev) {
    return ev.status !== 'completed' && new Date(ev.due_date) >= today;
  }).slice(0, 5);
  if (upcoming.length === 0) { bar.innerHTML = ''; return; }
  bar.innerHTML = upcoming.map(function(ev) {
    var col = CAT_COLORS[ev.category] || '#6b7280';
    var d = ev.due_date.substring(8,10);
    var time = ev.event_time ? ' ' + ev.event_time.substring(0,5) : '';
    return '<div onclick="showDetail('+ev.id+')" style="display:flex;align-items:center;gap:.4rem;padding:.35rem .65rem;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius-sm);cursor:pointer;font-size:.78rem;transition:all var(--transition);" onmouseover="this.style.borderColor=\''+col+'\'" onmouseout="this.style.borderColor=\'var(--border)\'">'
      + '<div style="width:3px;height:20px;background:'+col+';border-radius:2px;flex-shrink:0;"></div>'
      + '<div><span style="font-weight:600;color:var(--text);">'+d+time+'</span> '
      + '<span style="color:var(--text-muted);">'+ev.title+'</span></div></div>';
  }).join('');
}

function showDetail(id) {
  var ev = calEvents.find(function(e) { return e.id === id; });
  if (!ev) return;
  var col = CAT_COLORS[ev.category] || '#6b7280';
  var time = ev.event_time ? ev.event_time.substring(0,5) : '';
  var html = '<div style="display:flex;align-items:center;gap:.5rem;margin-bottom:.75rem;">'
    + '<div style="width:4px;height:28px;background:'+col+';border-radius:2px;"></div>'
    + '<div><div style="font-size:1rem;font-weight:600;color:var(--text);">'+ev.title+'</div>'
    + '<div style="font-size:.78rem;color:var(--text-muted);">'+ev.due_date+(time?' at '+time:'')+'</div></div></div>';
  html += '<div style="display:flex;flex-wrap:wrap;gap:.35rem;margin-bottom:.5rem;">';
  html += '<span style="display:inline-block;padding:.12rem .4rem;border-radius:20px;font-size:.68rem;font-weight:600;background:'+col+'20;color:'+col+';">'+ev.category.replace('_',' ')+'</span>';
  if (ev.status) html += '<span style="display:inline-block;padding:.12rem .4rem;border-radius:20px;font-size:.68rem;font-weight:500;background:var(--surface-2);color:var(--text-muted);">'+ev.status+'</span>';
  html += '</div>';
  if (ev.assigned_to) html += '<div style="font-size:.82rem;color:var(--text-muted);margin-bottom:.3rem;">Assigned: '+ev.assigned_to+'</div>';
  if (ev.notes) html += '<div style="font-size:.82rem;color:var(--text);margin-top:.5rem;padding:.5rem;background:var(--surface-2);border-radius:var(--radius-sm);">'+ev.notes+'</div>';

  document.getElementById('detail-content').innerHTML = html;
  var actions = '';
  if (canEdit) {
    actions = '<button class="btn btn-ghost btn-sm" onclick="editEvent('+ev.id+')">Edit</button>'
      + '<button class="btn btn-danger btn-sm" onclick="deleteEvent('+ev.id+')">Delete</button>';
    if (ev.status !== 'completed') {
      actions += '<button class="btn btn-primary btn-sm" onclick="completeEvent('+ev.id+')">Complete</button>';
    }
  }
  actions += '<button class="btn btn-ghost btn-sm" onclick="document.getElementById(\'detail-modal\').classList.remove(\'open\')">Close</button>';
  document.getElementById('detail-actions').innerHTML = actions;
  document.getElementById('detail-modal').classList.add('open');
}

function openAddModal(dateStr) {
  document.getElementById('modal-title').textContent = 'Add Event';
  document.getElementById('modal-save-btn').textContent = 'Add Event';
  document.getElementById('ev-id').value = '';
  document.getElementById('ev-title').value = '';
  document.getElementById('ev-date').value = dateStr || '';
  document.getElementById('ev-time').value = '';
  document.getElementById('ev-category').value = 'event';
  document.getElementById('ev-notes').value = '';
  document.getElementById('event-modal').classList.add('open');
}

function editEvent(id) {
  var ev = calEvents.find(function(e) { return e.id === id; });
  if (!ev) return;
  document.getElementById('detail-modal').classList.remove('open');
  document.getElementById('modal-title').textContent = 'Edit Event';
  document.getElementById('modal-save-btn').textContent = 'Save Changes';
  document.getElementById('ev-id').value = ev.id;
  document.getElementById('ev-title').value = ev.title;
  document.getElementById('ev-date').value = ev.due_date;
  document.getElementById('ev-time').value = ev.event_time || '';
  document.getElementById('ev-category').value = ev.category;
  document.getElementById('ev-notes').value = ev.notes || '';
  document.getElementById('event-modal').classList.add('open');
}

function closeModal() {
  document.getElementById('event-modal').classList.remove('open');
}

function saveEvent(e) {
  e.preventDefault();
  var id = document.getElementById('ev-id').value;
  var body = {
    title: document.getElementById('ev-title').value,
    due_date: document.getElementById('ev-date').value,
    event_time: document.getElementById('ev-time').value,
    category: document.getElementById('ev-category').value,
    notes: document.getElementById('ev-notes').value
  };
  var url, method;
  if (id) {
    url = '/api/calendar/events/' + id;
    method = 'PUT';
  } else {
    url = '/api/calendar/events';
    method = 'POST';
  }
  fetch(url, {method: method, headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)})
    .then(r => r.json())
    .then(data => {
      if (data.error) { alert(data.error); return; }
      closeModal();
      loadMonth();
    })
    .catch(err => alert('Error: ' + err));
}

function deleteEvent(id) {
  if (!confirm('Delete this event?')) return;
  fetch('/api/calendar/events/' + id, {method:'DELETE'})
    .then(r => r.json())
    .then(() => {
      document.getElementById('detail-modal').classList.remove('open');
      loadMonth();
    });
}

function completeEvent(id) {
  fetch('/api/calendar/events/' + id + '/complete', {method:'POST'})
    .then(r => r.json())
    .then(() => {
      document.getElementById('detail-modal').classList.remove('open');
      loadMonth();
    });
}
