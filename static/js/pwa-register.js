const pwaRegistration = {
  register() {
    if (!('serviceWorker' in navigator)) return;

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
