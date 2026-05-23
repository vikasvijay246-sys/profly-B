/**
 * Worker portal — offline-safe queue + connectivity banner
 */
(function () {
  const QUEUE_KEY = 'pf_worker_pending_actions';
  const banner = document.getElementById('offlineBanner');

  function isOnline() {
    return navigator.onLine;
  }

  function updateBanner() {
    if (!banner) return;
    banner.classList.toggle('show', !isOnline());
  }

  window.addEventListener('online', updateBanner);
  window.addEventListener('offline', updateBanner);
  updateBanner();

  function getQueue() {
    try {
      return JSON.parse(localStorage.getItem(QUEUE_KEY) || '[]');
    } catch (e) {
      return [];
    }
  }

  function setQueue(q) {
    localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
  }

  window.workerQueueAction = function (payload) {
    const q = getQueue();
    q.push({ ...payload, ts: Date.now() });
    setQueue(q);
  };

  async function flushQueue() {
    if (!isOnline()) return;
    const q = getQueue();
    if (!q.length) return;
    const remaining = [];
    for (const item of q) {
      try {
        const res = await fetch(item.url, {
          method: item.method || 'POST',
          body: item.body,
          headers: item.headers || {},
          credentials: 'same-origin',
        });
        if (!res.ok) remaining.push(item);
      } catch (e) {
        remaining.push(item);
      }
    }
    setQueue(remaining);
  }

  window.addEventListener('online', flushQueue);
  if (isOnline()) flushQueue();

  // Unread badge poll
  const badgeEl = document.querySelector('.worker-topbar .worker-badge');
  if (badgeEl && window.location.pathname.startsWith('/worker')) {
    setInterval(async () => {
      try {
        const r = await fetch('/worker/api/unread');
        const d = await r.json();
        if (d.unread > 0) {
          badgeEl.textContent = d.unread > 9 ? '9+' : d.unread;
          badgeEl.style.display = '';
        } else {
          badgeEl.style.display = 'none';
        }
      } catch (e) { /* ignore */ }
    }, 30000);
  }

  // Socket notifications for logged-in workers
  if (typeof io !== 'undefined' && document.body.dataset.userId) {
    const socket = io({ transports: ['websocket', 'polling'] });
    socket.emit('join_user_room', { user_id: parseInt(document.body.dataset.userId, 10) });
    socket.on('notification', function (data) {
      if (banner) {
        banner.textContent = (data.title || 'New alert') + ': ' + (data.body || '');
        banner.classList.add('show');
        setTimeout(() => banner.classList.remove('show'), 5000);
      }
    });
  }
})();
