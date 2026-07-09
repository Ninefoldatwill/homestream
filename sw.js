/*!
 * HomeStream Service Worker v5.0.0
 * PWA offline support — caches static shell, falls back to offline.html
 * Original implementation, W3C Service Worker API only
 */

const CACHE_NAME = 'homestream-v5-0-0';
const PRECACHE_ASSETS = [
  '/',
  '/offline.html',
  '/assets/icon-192.png',
  '/assets/icon-512.png',
  '/assets/mobile.css'
];

// --- Install: pre-cache core static shell ---
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(PRECACHE_ASSETS).catch(() => {})
    )
  );
  self.skipWaiting();
});

// --- Activate: purge old caches ---
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

// --- Fetch: cache-first for static, network-first for API ---
self.addEventListener('fetch', (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // Never cache API calls or WebSocket upgrades
  if (url.pathname.startsWith('/api/') || request.url.includes('/ws') || url.protocol === 'ws:') {
    return;
  }

  // Only handle GET
  if (request.method !== 'GET') {
    return;
  }

  event.respondWith(
    caches.match(request).then((cached) => {
      if (cached) return cached;
      return fetch(request).then((response) => {
        // Cache same-origin successful responses
        if (response.ok && url.origin === self.location.origin) {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(request, clone));
        }
        return response;
      }).catch(() => {
        // Offline fallback for navigation requests
        if (request.mode === 'navigate') {
          return caches.match('/offline.html');
        }
        return new Response('', { status: 503, statusText: 'Offline' });
      });
    })
  );
});
