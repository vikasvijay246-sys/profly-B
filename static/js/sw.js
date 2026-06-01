const CACHE_VERSION = '2026-06-01-v2';
const PRECACHE = `propflow-static-${CACHE_VERSION}`;
const RUNTIME = `propflow-runtime-${CACHE_VERSION}`;

const PRECACHE_URLS = [
  '/static/css/style.css',
  '/static/css/pwa.css',
  '/static/js/main.js',
  '/static/js/worker.js',
  // '/static/icons/icon-192x192.png',+
  '/static/icons/icon-512x512.png',
  // '/static/icons/maskable-icon-192x192.png',
  // '/static/icons/maskable-icon-512x512.png',
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

  // Navigation requests should always try network first and fall back to offline page.
  // Other requests may be skipped for caching if they match NEVER_CACHE_PATHS.
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request));
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
  // Try network with a short timeout, fall back to a cached navigation if available,
  // then finally serve the offline shell.
  const timeoutMs = 8000;
  const fetchPromise = fetch(request, { credentials: 'same-origin' });
  const timeoutPromise = new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), timeoutMs));

  try {
    const response = await Promise.race([fetchPromise, timeoutPromise]);
    if (response && response.ok) {
      return response;
    }
  } catch (err) {
    // swallow and try cache fallbacks below
  }

  // Try to return a cached version of the requested page (if available)
  try {
    const cached = await caches.match(request);
    if (cached) return cached;
  } catch (err) {}

  // Finally return the offline shell
  const cache = await caches.match('/offline.html');
  return cache || new Response('Offline', { status: 503, statusText: 'Offline' });
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(PRECACHE);
  const cachedResponse = await cache.match(request);

  const networkPromise = fetch(request).then(response => {
    if (response && response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  }).catch(() => null);

  return cachedResponse || (await networkPromise) || new Response('Offline', { status: 503 });
}
