/* StockIQ Service Worker
   ------------------------------------------------------------------
   Rule #1: live market data NEVER comes from the cache.
   Only the app shell (HTML / icons / manifest / static CDN assets)
   is cached, so the app opens instantly and still works partially
   when the network is down.
   ------------------------------------------------------------------ */

/* גרסה מוגדלת בכל שינוי במעטפת. הקובץ הראשי עבר מ-stocks.html ל-index.html,
   ובלי הגדלת הגרסה מתקינים קיימים היו נשארים עם המעטפת הישנה במטמון. */
const VERSION      = 'v7';
const SHELL_CACHE  = 'stockiq-shell-' + VERSION;
const ASSET_CACHE  = 'stockiq-assets-' + VERSION;

const SHELL_FILES = [
  './',
  './index.html',
  './manifest.json',
  './icon-192.png',
  './icon-512.png'
];

/* Hosts that must ALWAYS hit the network — live stock data, news, AI.
   Requests to these are not intercepted at all. */
const NEVER_CACHE = [
  'onrender.com',
  'ngrok-free.dev',
  'ngrok-free.app',
  'ngrok.io',
  'finnhub.io',
  'groq.com',
  'yahoo.com'
];

/* Static third-party assets that are safe to cache */
const CDN_ALLOW = [
  'cdnjs.cloudflare.com',
  'fonts.googleapis.com',
  'fonts.gstatic.com'
];

/* ---------- install ---------- */
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL_CACHE);
    // cache files one by one so a single 404 can't break the install
    await Promise.all(SHELL_FILES.map(async file => {
      try { await cache.add(new Request(file, { cache: 'reload' })); }
      catch (err) { console.warn('[SW] skipped:', file, err); }
    }));
    await self.skipWaiting();
  })());
});

/* ---------- activate: drop old versions ---------- */
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(
      keys.filter(k => k !== SHELL_CACHE && k !== ASSET_CACHE)
          .map(k => caches.delete(k))
    );
    await self.clients.claim();
  })());
});

/* ---------- fetch ---------- */
self.addEventListener('fetch', event => {
  const req = event.request;

  // only GET is ever cached
  if (req.method !== 'GET') return;

  let url;
  try { url = new URL(req.url); } catch (e) { return; }

  // live data -> straight to the network, untouched
  if (NEVER_CACHE.some(host => url.hostname.includes(host))) return;

  const sameOrigin = url.origin === self.location.origin;

  // any other third-party host we don't explicitly trust -> untouched
  if (!sameOrigin && !CDN_ALLOW.includes(url.hostname)) return;

  // page loads: network first, cache only as an offline fallback
  if (req.mode === 'navigate') {
    event.respondWith(networkFirst(req));
    return;
  }

  // our own static files: serve fast from cache, refresh in the background
  if (sameOrigin) {
    event.respondWith(staleWhileRevalidate(req, SHELL_CACHE));
    return;
  }

  // trusted CDN files (Chart.js, fonts): cache first
  event.respondWith(cacheFirst(req, ASSET_CACHE));
});

/* ---------- strategies ---------- */
async function networkFirst(req) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const fresh = await fetch(req);
    if (fresh && fresh.ok) cache.put(req, fresh.clone());
    return fresh;
  } catch (err) {
    const cached = await cache.match(req)
                || await cache.match('./index.html')
                || await cache.match('./');
    if (cached) return cached;
    throw err;
  }
}

async function staleWhileRevalidate(req, cacheName) {
  const cache  = await caches.open(cacheName);
  const cached = await cache.match(req);
  const network = fetch(req).then(res => {
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  }).catch(() => null);
  return cached || network || fetch(req);
}

async function cacheFirst(req, cacheName) {
  const cache  = await caches.open(cacheName);
  const cached = await cache.match(req);
  if (cached) return cached;
  const res = await fetch(req);
  if (res && (res.ok || res.type === 'opaque')) cache.put(req, res.clone());
  return res;
}

/* allow the page to activate a waiting worker immediately */
self.addEventListener('message', event => {
  if (event.data === 'SKIP_WAITING') self.skipWaiting();
});
