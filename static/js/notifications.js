/**
 * Native-style Notifications for PropFlow
 * Mimics native app notification behavior on mobile
 */
const PropFlowNotifications = (() => {
  const notifContainer = () => {
    let container = document.getElementById('propflow-native-notif-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'propflow-native-notif-container';
      container.style.cssText = `
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        z-index: 10000;
        padding: 0;
        margin: 0;
        display: flex;
        flex-direction: column;
        gap: 8px;
        padding-top: env(safe-area-inset-top);
        pointer-events: none;
      `;
      document.body.appendChild(container);
    }
    return container;
  };

  const createNotifEl = (title, body, type = 'info', duration = 4000) => {
    const el = document.createElement('div');
    const bgColor = type === 'success' ? '#10b981' : type === 'error' ? '#ef4444' : type === 'warning' ? '#f59e0b' : '#3b82f6';
    const icon = type === 'success' ? '✓' : type === 'error' ? '✕' : type === 'warning' ? '!' : 'ℹ';

    el.style.cssText = `
      background: ${bgColor};
      color: white;
      padding: 14px 16px;
      margin: 8px 8px 0;
      border-radius: 12px;
      box-shadow: 0 4px 12px rgba(0,0,0,0.2);
      font-weight: 600;
      font-size: 14px;
      line-height: 1.4;
      max-width: calc(100% - 16px);
      animation: notifSlideDown 0.3s ease;
      pointer-events: auto;
      cursor: pointer;
    `;
    el.innerHTML = `<div style="display:flex;gap:8px;align-items:flex-start;"><span style="font-size:16px;flex-shrink:0;">${icon}</span><div style="flex:1;"><strong>${title}</strong>${body ? `<div style="font-size:12px;opacity:0.9;margin-top:2px;">${body}</div>` : ''}</div></div>`;

    el.addEventListener('click', () => {
      el.style.animation = 'notifSlideUp 0.3s ease forwards';
      setTimeout(() => el.remove(), 300);
    });

    if (duration > 0) {
      setTimeout(() => {
        if (el.parentElement) {
          el.style.animation = 'notifSlideUp 0.3s ease forwards';
          setTimeout(() => el.remove(), 300);
        }
      }, duration);
    }

    return el;
  };

  return {
    show(title, body = '', type = 'info', duration = 4000) {
      const container = notifContainer();
      const el = createNotifEl(title, body, type, duration);
      container.appendChild(el);
    },

    success(title, body = '', duration = 3000) {
      this.show(title, body, 'success', duration);
    },

    error(title, body = '', duration = 5000) {
      this.show(title, body, 'error', duration);
    },

    warning(title, body = '', duration = 4000) {
      this.show(title, body, 'warning', duration);
    },

    info(title, body = '', duration = 3000) {
      this.show(title, body, 'info', duration);
    },

    /**
     * Request permission for Web Push Notifications (native system notifications)
     */
    async requestPermission() {
      if (!('Notification' in window)) {
        console.warn('Browser does not support Notifications API');
        return false;
      }

      if (Notification.permission === 'granted') {
        return true;
      }

      if (Notification.permission !== 'denied') {
        try {
          const permission = await Notification.requestPermission();
          return permission === 'granted';
        } catch (err) {
          console.warn('Notification permission denied:', err);
          return false;
        }
      }

      return false;
    },

    /**
     * Send native system notification (if permission granted)
     */
    sendNative(title, options = {}) {
      if (Notification.permission === 'granted') {
        const notif = new Notification(title, {
          icon: '/static/icons/icon-192x192.png',
          badge: '/static/icons/maskable-icon-192x192.png',
          tag: 'propflow-notification',
          requireInteraction: false,
          ...options
        });

        notif.addEventListener('click', () => {
          window.focus();
          notif.close();
        });

        return notif;
      }
    },

    /**
     * Vibrate device if supported
     */
    vibrate(pattern = [200]) {
      if ('vibrate' in navigator) {
        navigator.vibrate(pattern);
      }
    }
  };
})();

// Add CSS animations
if (!document.getElementById('propflow-notif-styles')) {
  const style = document.createElement('style');
  style.id = 'propflow-notif-styles';
  style.textContent = `
    @keyframes notifSlideDown {
      from {
        opacity: 0;
        transform: translateY(-20px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }
    @keyframes notifSlideUp {
      from {
        opacity: 1;
        transform: translateY(0);
      }
      to {
        opacity: 0;
        transform: translateY(-20px);
      }
    }
  `;
  document.head.appendChild(style);
}

// Make globally available
window.PropFlowNotifications = PropFlowNotifications;
