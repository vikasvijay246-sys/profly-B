const CACHE_VERSION = '2026-06-01-v2';
const PRECACHE = `propflow-static-${CACHE_VERSION}`;
const RUNTIME = `propflow-runtime-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  '/static/css/style.css',
  '/static/css/pwa.css',
  '/static/js/main.js',
  '/static/js/pwa-register.js',
  '/static/js/worker.js',
  '/static/icons/icon-192x192.png',
  '/static/icons/icon-192x192-maskable.png',
  '/static/icons/icon-512x512.png',
  '/static/icons/icon-512x512-maskable.png',
  '/manifest.json',
  '/offline.html'
];

const STATIC_ASSET_REGEX = /\.(?:js|css|png|jpg|jpeg|gif|webp|svg|ico|json|woff2?|ttf|eot|otf)$/i;
const NEVER_CACHE_PATHS = ['/login', '/logout', '/auth', '/api', '/chat', '/socket.io'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(PRECACHE)
      .then(cache => Promise.all(PRECACHE_URLS.map( async url => {
        try{
          await cache.add(url);
          console.log('Cached:',url);
        }
        catch(err){
          console.error('Failed to cache:',url,err);
        }
      })
      )
      ).then(() => self.skipWaiting())
    );
  }); 



// self.addEventListener('install', event => {
//   event.waitUntil(
//     caches.open(PRECACHE)
//       .then(cache => cache.addAll(PRECACHE_URLS))
//       .then(() => self.skipWaiting())
//   );
  
// });

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => Promise.all(
      keys.filter(key => key !== PRECACHE && key !== RUNTIME)
          .map(key => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname === '/sw.js') return;

  // Navigation requests should serve cached pages immediately when available,
  // while updating the cache in the background.
  if (request.mode === 'navigate') {
    event.respondWith(navigateHandler(request));
    return;
  }

  if (NEVER_CACHE_PATHS.some(path => url.pathname.startsWith(path))) {
    return;
  }

  if (STATIC_ASSET_REGEX.test(url.pathname) || url.pathname.startsWith('/static/')) {
    event.respondWith(staleWhileRevalidate(request));
    return;
  }
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

async function networkFirst(request) {
  // Try network with a reasonable timeout
  const timeoutMs = 4000;
  const fetchPromise = fetch(request, { credentials: 'same-origin' });
  const timeoutPromise = new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs));

  try {
    const response = await Promise.race([fetchPromise, timeoutPromise]);
    // Return any response (including errors like 401, 404, etc.)
    // DO NOT check response.ok - errors should be passed through to the client
    if (response) {
      return response;
    }
  } catch (err) {
    // Network error or timeout occurred
    console.debug('[SW] Network failed:', err.message);
  }

  // Try to return a cached version of the requested page (if available)
  try {
    const cached = await caches.match(request);
    if (cached) return cached;
  } catch (err) {}

  // Last resort: serve offline shell
  try {
    const offlineCache = await caches.match('/offline.html');
    if (offlineCache) return offlineCache;
  } catch (err) {}

  return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
}

async function trimCache(cacheName, maxItems) {
  try {
    const cache = await caches.open(cacheName);
    const keys = await cache.keys();
    if (keys.length > maxItems) {
      for (let i = 0; i < keys.length - maxItems; i++) {
        await cache.delete(keys[i]);
      }
    }
  } catch (err) {
    console.debug('[SW] trimCache error', err);
  }
}

async function navigateHandler(request) {
  const cache = await caches.open(RUNTIME);
  const cached = await cache.match(request);

  const networkPromise = fetch(request, { credentials: 'same-origin' })
    .then(async response => {
      if (response && response.ok) {
        await cache.put(request, response.clone());
        trimCache(RUNTIME, 50);
      }
      return response;
    })
    .catch(() => null);

  if (cached) {
    networkPromise.catch(() => {});
    return cached;
  }

  const timeoutMs = 4000;
  const timeoutPromise = new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs));

  try {
    const response = await Promise.race([networkPromise, timeoutPromise]);
    if (response) return response;
  } catch (err) {
    console.debug('[SW] Navigate failed:', err.message);
  }

  const offlineCache = await caches.match('/offline.html');
  return offlineCache || new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(PRECACHE);
  const cachedResponse = await cache.match(request);

  const networkPromise = fetch(request).then(async response => {
    if (response && response.ok) {
      await cache.put(request, response.clone());
      trimCache(PRECACHE, 200);
    }
    return response;
  }).catch(() => null);

  return cachedResponse || (await networkPromise) || new Response('Offline', { status: 503 });
}
