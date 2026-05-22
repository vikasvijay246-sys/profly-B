/* ── Modal helpers ────────────────────────────────────── */
function openModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.remove('hidden');
  // Trap focus on first input for accessibility
  const first = el.querySelector('input:not([type=hidden]), select, textarea');
  if (first) setTimeout(() => first.focus(), 80);
}

function closeModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.add('hidden');
}

// Close on backdrop click
document.addEventListener('click', function(e) {
  if (e.target.classList.contains('modal-backdrop')) {
    e.target.classList.add('hidden');
  }
});

// Close on Escape
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-backdrop:not(.hidden)').forEach(el => {
      el.classList.add('hidden');
    });
  }
});

/* ── Sidebar (mobile) ─────────────────────────────────── */
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
  document.getElementById('sidebarOverlay').classList.toggle('open');
}
function closeSidebar() {
  document.getElementById('sidebar').classList.remove('open');
  document.getElementById('sidebarOverlay').classList.remove('open');
}

/* ── Dark mode ────────────────────────────────────────── */
function toggleDark() {
  const html   = document.documentElement;
  const isDark = html.dataset.theme === 'dark';
  html.dataset.theme = isDark ? 'light' : 'dark';
  localStorage.setItem('pf_theme', html.dataset.theme);
  const btn = document.getElementById('darkToggle');
  if (btn) btn.textContent = isDark ? '🌙' : '☀️';
}

// Apply saved theme immediately (called from base.html inline script too)
(function() {
  const saved = localStorage.getItem('pf_theme');
  if (saved) {
    document.documentElement.dataset.theme = saved;
    const btn = document.getElementById('darkToggle');
    if (btn) btn.textContent = saved === 'dark' ? '☀️' : '🌙';
  }
})();

/* ── Auto-dismiss flash messages ──────────────────────── */
document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.flash').forEach(function(el) {
    setTimeout(function() {
      el.style.transition = 'opacity .4s';
      el.style.opacity    = '0';
      setTimeout(() => el.remove(), 400);
    }, 5000);
  });
});

/* ── Join user-specific SocketIO room ─────────────────── */
// Called from base.html after socket is initialised
if (typeof socket !== 'undefined') {
  socket.on('connect', function() {
    socket.emit('join_user_room');
    const reciverId= document.getElementById('receiver_id') ?.value;
    if(reciverId){
      socket.emit('join_user_room',{receiver_id:reciverId});
    }     
  });
}
// socket.on("new_message", function(msg) {console.log("New message received:", msg);appendMessage(msg);});

async function toggleTenantStatus(tenantId) {
  const statusField = document.getElementById('tenantStatus');
  if (!statusField) {
    return alert('Tenant status information is unavailable.');
  }

  const currentStatus = statusField.value === 'inactive' ? 'inactive' : 'active';
  const targetValue = currentStatus === 'active' ? '0' : '1';
  const actionLabel = currentStatus === 'active' ? 'Deactivate' : 'Reactivate';

  if (currentStatus === 'active' && !confirm(
      'Deactivate this tenant? This preserves payment history and room assignments but hides active tenant data from the dashboard.'
  )) {
    return;
  }

  try {
    const response = await fetch(`/owner/tenants/${tenantId}/edit`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      credentials: 'same-origin',
      body: new URLSearchParams({ is_active: targetValue }).toString(),
    });

    if (!response.ok) {
      throw new Error('Network response was not ok');
    }

    if (typeof socket !== 'undefined' && socket && socket.connected) {
      socket.emit('tenant_status_toggled', {
        tenant_id: tenantId,
        status: targetValue === '1' ? 'active' : 'inactive',
      });
    }

    window.location.reload();
  } catch (error) {
    console.error('toggleTenantStatus error', error);
    alert(`${actionLabel} failed. Please refresh and try again.`);
  }
}

