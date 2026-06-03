function openModal(e){const l=document.getElementById(e);if(!l)return;l.classList.remove('hidden');setTimeout(()=>{const e=l.querySelector('input:not([type=hidden]), select, textarea');e&&e.focus()},80)}function closeModal(e){const l=document.getElementById(e);l&&l.classList.add('hidden')}

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
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  sidebar?.classList.toggle('open');
  overlay?.classList.toggle('open');
}
function closeSidebar() {
  const sidebar = document.getElementById('sidebar');
  const overlay = document.getElementById('sidebarOverlay');
  sidebar?.classList.remove('open');
  overlay?.classList.remove('open');
}

window.addEventListener('resize', () => {
  if (window.innerWidth > 768) {
    closeSidebar();
  }
});

window.addEventListener('popstate', closeSidebar);
window.addEventListener('hashchange', closeSidebar);
window.addEventListener('pageshow', () => {
  closeSidebar();
});

document.addEventListener('click', function(event) {
  const overlay = document.getElementById('sidebarOverlay');
  if (overlay && event.target === overlay) {
    closeSidebar();
  }
});

document.addEventListener('DOMContentLoaded', function() {
  document.querySelectorAll('.sidebar-nav a').forEach(function(link) {
    link.addEventListener('click', closeSidebar);
  });
});

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

// Lightweight image lazy-loading fallback: mark images as 'lazy' unless explicitly set
document.addEventListener('DOMContentLoaded', function() {
  try {
    document.querySelectorAll('img').forEach(img => {
      if (!img.hasAttribute('loading')) img.setAttribute('loading', 'lazy');
    });
  } catch (e) { /* no-op */ }
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

/* ──────────────────────────────────────────────────────────
   ┏━━━ PWA & App-Like Enhancements ━━━┓
   └────────────────────────────────────┘
   Smooth navigation, prefetching, native-like interactions
   ────────────────────────────────────────────────────────── */

/**
 * Link Prefetching Strategy
 * Preload links in viewport for instant navigation feel
 */
(function() {
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
  const isDataSaving = connection && (connection.saveData || /(2g|slow-2g)/i.test(connection.effectiveType || ''));
  if (isDataSaving) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && entry.target.tagName === 'A') {
        const link = entry.target;
        const href = link.getAttribute('href');
        if (shouldPrefetch(link, href)) {
          prefetchLink(href);
        }
      }
    });
  }, { rootMargin: '50px' });

  document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('a').forEach(link => {
      const href = link.getAttribute('href');
      if (shouldPrefetch(link, href)) {
        observer.observe(link);
      }
    });
  });

  function shouldPrefetch(link, href) {
    if (!href || href.startsWith('#') || href.startsWith('javascript:')) return false;
    if (link.target && link.target !== '_self') return false;
    if (link.hasAttribute('download')) return false;
    if (link.rel && (link.rel.includes('external') || link.rel.includes('no-prefetch'))) return false;
    if (link.hasAttribute('data-no-prefetch')) return false;
    try {
      const url = new URL(href, location.href);
      // Avoid prefetching authentication-related endpoints which may perform state-changing GETs
      if (url.pathname === '/logout' || url.pathname === '/login' || url.pathname.startsWith('/auth')) return false;
      return url.origin === location.origin && url.pathname !== location.pathname;
    } catch (err) {
      return false;
    }
  }

  function prefetchLink(href) {
    if (document.querySelector(`link[rel="prefetch"][href="${href}"]`)) return;
    const link = document.createElement('link');
    link.rel = 'prefetch';
    link.href = href;
    link.as = 'document';
    document.head.appendChild(link);
  }
})();

/**
 * Smooth Page Transitions
 * Add enter animation to page content on load
 */
document.addEventListener('DOMContentLoaded', () => {
  const content = document.querySelector('.page-content');
  if (content) {
    content.style.animation = 'slideInContent 0.3s cubic-bezier(0.4, 0, 0.2, 1)';
  }
});

/**
 * History Back Button Enhancement
 * Smooth exit animation before navigation
 */
window.addEventListener('beforeunload', () => {
  const content = document.querySelector('.page-content');
  if (content) {
    content.style.animation = 'pageExit 0.2s cubic-bezier(0.4, 0, 0.2, 1) forwards';
  }
});

/**
 * Touch Feedback & Haptic
 * Improve tap response on touch devices
 */
document.addEventListener('touchstart', (e) => {
  const target = e.target.closest('button, a, .nav-item, .icon-btn, input, textarea');
  if (target) {
    target.style.transform = 'scale(0.97)';
    // Haptic feedback on capable devices
    if (navigator.vibrate) {
      navigator.vibrate(5);
    }
  }
}, { passive: true });

document.addEventListener('touchend', (e) => {
  const target = e.target.closest('button, a, .nav-item, .icon-btn, input, textarea');
  if (target) {
    target.style.transform = '';
  }
}, { passive: true });

/**
 * Loading State Manager
 * Show spinner on form submit
 */
document.addEventListener('submit', (e) => {
  const btn = e.target.querySelector('button[type="submit"]');
  if (btn && !btn.disabled) {
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> Processing...';
    
    // Restore after 30s timeout (fallback)
    setTimeout(() => {
      btn.disabled = false;
      btn.textContent = originalText;
    }, 30000);
  }
});

/**
 * Sidebar Smooth Close on Link Click
 * Provide instant feedback on mobile navigation
 */
document.addEventListener('click', (e) => {
  const navLink = e.target.closest('.nav-item');
  if (navLink && window.innerWidth <= 768) {
    closeSidebar();
  }
});

/**
 * Viewport Meta Adjustment
 * Fix zoom on input focus (iOS)
 */
let isIOSVirtualKeyboardOpen = false;
window.visualViewport?.addEventListener('resize', () => {
  const viewport = document.querySelector('meta[name="viewport"]');
  if (window.innerHeight < window.visualViewport.height * 0.9) {
    // Virtual keyboard is open
    if (!isIOSVirtualKeyboardOpen) {
      isIOSVirtualKeyboardOpen = true;
      viewport.setAttribute('content', 'width=device-width, initial-scale=1.0, viewport-fit=cover, user-scalable=no');
    }
  } else {
    isIOSVirtualKeyboardOpen = false;
  }
});

/**
 * Prevent Overscroll Bounce
 * Disable iOS pull-to-refresh gesture
 */
let lastY = 0;
document.addEventListener('touchstart', (e) => {
  lastY = e.touches[0].clientY;
}, { passive: true });

document.addEventListener('touchmove', (e) => {
  const scrollTop = document.querySelector('.page-content')?.scrollTop || window.scrollY;
  if (scrollTop === 0 && e.touches[0].clientY > lastY) {
    e.preventDefault();
  }
}, { passive: false });

/**
 * Network Status Indicator
 * Visual feedback for offline state with recovery
 */
let offlineNotificationShown = false;

function showOfflineNotification() {
  if (offlineNotificationShown) return;
  offlineNotificationShown = true;
  
  const notification = document.createElement('div');
  notification.id = 'offline-notification';
  notification.style.cssText = `
    position: fixed;
    top: 0;
    left: 0;
    right: 0;
    z-index: 9999;
    background: #dc2626;
    color: white;
    padding: 12px 16px;
    text-align: center;
    font-weight: 500;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  `;
  notification.textContent = '⚠️ No internet connection. Changes may not sync.';
  document.body.insertBefore(notification, document.body.firstChild);
}

function removeOfflineNotification() {
  const notification = document.getElementById('offline-notification');
  if (notification) {
    notification.style.animation = 'slideUp 0.3s ease forwards';
    setTimeout(() => notification.remove(), 300);
  }
  offlineNotificationShown = false;
}

let hadOfflineState = false;
window.addEventListener('offline', () => {
  console.warn('App lost internet connection');
  hadOfflineState = true;
  showOfflineNotification();
});

window.addEventListener('online', () => {
  console.log('App is back online');
  if (hadOfflineState) {
    removeOfflineNotification();
    hadOfflineState = false;
    if (!window.location.href.includes('/login')) {
      setTimeout(() => window.location.reload(), 500);
    }
    return;
  }

  removeOfflineNotification();
});

/**
 * Focus Management
 * Ensure focus management for accessibility and keyboard navigation
 */
document.addEventListener('keydown', (e) => {
  if (e.key === 'Tab') {
    document.body.classList.add('keyboard-nav-active');
  }
});

document.addEventListener('click', () => {
  document.body.classList.remove('keyboard-nav-active');
});

/**
 * Optimize Image Loading
 * Lazy load images for faster initial paint
 */
if ('IntersectionObserver' in window) {
  const imageObserver = new IntersectionObserver((entries, observer) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        const img = entry.target;
        img.src = img.dataset.src || img.src;
        img.classList.add('loaded');
        observer.unobserve(img);
      }
    });
  });

  document.querySelectorAll('img[data-src]').forEach(img => {
    imageObserver.observe(img);
  });
}

console.log('PropFlow PWA & App-Like enhancements loaded');

