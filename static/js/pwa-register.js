const pwaRegistration = {
  register() {
    if (!('serviceWorker' in navigator)) return;

    // Verify actual network connectivity before proceeding
    this.verifyNetworkStatus();

    window.addEventListener('load', async () => {
      try {
        const registration = await navigator.serviceWorker.register('/sw.js', {
          scope: '/'
        });

        this.watchUpdate(registration);
        this.listenForWaitingWorker(registration);
      } catch (error) {
        console.warn('PWA registration failed:', error);
      }
    });

    window.addEventListener('beforeinstallprompt', event => {
      event.preventDefault();
      window.deferredInstallPrompt = event;
    });
  },

  verifyNetworkStatus() {
    // Check both navigator.onLine and actual network connectivity
    const checkConnection = async () => {
      try {
        // Use a simple HEAD request to verify network connectivity
        // This bypasses aggressive service worker timeouts
        const response = await fetch('/manifest.json', {
          method: 'HEAD',
          credentials: 'omit',
          cache: 'no-cache',
          mode: 'no-cors'
        });
        return response.ok || response.type === 'opaque';
      } catch (err) {
        return false;
      }
    };

    // If navigator says we're offline, respect that
    if (!navigator.onLine) {
      console.warn('[PWA] Device reports offline status');
      return;
    }

    // Periodically verify connectivity (every 30 seconds)
    setInterval(async () => {
      const isConnected = await checkConnection();
      window.__pf_has_network = isConnected;
      if (!isConnected && navigator.onLine) {
        console.warn('[PWA] Lost network connectivity despite navigator.onLine=true');
      }
    }, 30000);
  },

  watchUpdate(registration) {
    registration.addEventListener('updatefound', () => {
      const newWorker = registration.installing;
      if (!newWorker) return;

      newWorker.addEventListener('statechange', () => {
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          this.notifyUpdateReady(newWorker);
        }
      });
    });

    navigator.serviceWorker.addEventListener('controllerchange', () => {
      window.location.reload();
    });
  },

  listenForWaitingWorker(registration) {
    if (registration.waiting) {
      this.notifyUpdateReady(registration.waiting);
    }
  },

  notifyUpdateReady(worker) {
    if (!worker) return;
    const message = 'A new version of PropFlow is available. Reload to apply the update.';
    if (window.showToast) {
      showToast(message, {
        action: 'Reload',
        onAction: () => this.applyUpdate(worker)
      });
      return;
    }
    if (confirm(message)) {
      this.applyUpdate(worker);
    }
  },

  applyUpdate(worker) {
    worker.postMessage({ type: 'SKIP_WAITING' });
  }
};

pwaRegistration.register();
