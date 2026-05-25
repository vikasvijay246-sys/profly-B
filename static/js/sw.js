/**
 * PropFlow Service Worker
 * Handles offline caching, asset caching, and app-like experience
 */

const CACHE_NAME = 'propflow-v1';
const RUNTIME_CACHE = 'propflow-runtime-v1';
const STATIC_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/css/worker.css',
  '/static/js/main.js',
  '/static/js/worker.js',
  '/manifest.json'
];

// Install event: cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('Some assets failed to cache during install:', err);
        return Promise.resolve();
      });
    }).then(() => self.skipWaiting())
  );
});

// Activate event: clean up old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME && cacheName !== RUNTIME_CACHE) {
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch event: network first, fall back to cache
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // Skip non-GET requests
  if (request.method !== 'GET') {
    return;
  }

  // Skip browser extensions and cross-origin requests
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    return;
  }

  // API calls: network first, cache fallback
  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/chat')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (!response || response.status !== 200) {
            return response;
          }
          const responseClone = response.clone();
          caches.open(RUNTIME_CACHE).then(cache => {
            cache.put(request, responseClone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(request).then(cached => {
            return cached || new Response(
              JSON.stringify({ error: 'Offline. Limited functionality.' }),
              { status: 503, contentType: 'application/json' }
            );
          });
        })
    );
    return;
  }

  // Static assets: cache first, network fallback
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(request).then(cached => {
        return cached || fetch(request).then(response => {
          if (!response || response.status !== 200) {
            return response;
          }
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(request, responseClone);
          });
          return response;
        });
      }).catch(() => {
        return new Response('Asset not available offline', { status: 404 });
      })
    );
    return;
  }

  // HTML pages: network first, cache fallback
  event.respondWith(
    fetch(request)
      .then(response => {
        if (!response || response.status !== 200 || response.type === 'error') {
          return response;
        }
        const responseClone = response.clone();
        caches.open(RUNTIME_CACHE).then(cache => {
          cache.put(request, responseClone);
        });
        return response;
      })
      .catch(() => {
        return caches.match(request).then(cached => {
          return cached || new Response(
            'You are offline. This page was not cached.',
            { status: 503 }
          );
        });
      })
  );
});

// Handle messages from clients
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
