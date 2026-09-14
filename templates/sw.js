// sw.js — Instagram Digest Service Worker
// Persistent video caching with zero-copy HTTP 206 Partial Content range slicing for iOS WebKit.
// Bulk "Download All" runs on the page (player.js startFullDownload): browsers
// cap how long a message event may be extended, so a 3 GB loop inside
// waitUntil was terminated silently part-way through.

const CACHE_NAME = 'ig-digest-media-v1';
const PRECACHE_CONCURRENCY = 2;
const BLOB_MEMO_MAX = 2;

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.map((k) => {
          if (k !== CACHE_NAME) {
            return caches.delete(k);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// RFC 7233 range handling: single byte-range-spec only (suffix, open-ended,
// closed). Unsatisfiable or multipart ranges get 416 + `bytes */size` so
// Safari AVFoundation seeks correctly instead of stalling on a 1-byte 206.
function rangeNotSatisfiable(total) {
  return new Response('Range Not Satisfiable', {
    status: 416,
    statusText: 'Range Not Satisfiable',
    headers: {
      'Content-Range': `bytes */${total}`,
      'Accept-Ranges': 'bytes',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

// Helper to construct a synthetic 206 Partial Content response using zero-copy Blob.slice
function createPartialBlobResponse(blob, rangeHeader, contentType) {
  const total = blob.size;
  if (total === 0) {
    return rangeNotSatisfiable(0);
  }

  // Multipart ranges are not supported: fail per RFC 7233, never clamp.
  const raw = (rangeHeader || '').trim();
  if (raw.includes(',')) {
    return rangeNotSatisfiable(total);
  }

  const matches = raw.match(/^bytes=(\d*)-(\d*)$/);
  if (!matches || (matches[1] === '' && matches[2] === '')) {
    return rangeNotSatisfiable(total);
  }

  let start;
  let end;
  if (matches[1] === '') {
    // Suffix range: last N bytes.
    const suffix = parseInt(matches[2], 10);
    if (!suffix) {
      return rangeNotSatisfiable(total);
    }
    start = Math.max(0, total - suffix);
    end = total - 1;
  } else {
    start = parseInt(matches[1], 10);
    end = matches[2] === '' ? total - 1 : parseInt(matches[2], 10);
  }

  // Unsatisfiable: start beyond EOF or inverted range -> 416, never 206.
  if (start >= total || start > end) {
    return rangeNotSatisfiable(total);
  }
  end = Math.min(end, total - 1);
  const chunk = blob.slice(start, end + 1);

  return new Response(chunk, {
    status: 206,
    statusText: 'Partial Content',
    headers: {
      'Content-Type': contentType || 'video/mp4',
      'Content-Range': `bytes ${start}-${end}/${total}`,
      'Accept-Ranges': 'bytes',
      'Content-Length': String(chunk.size),
      'Access-Control-Allow-Origin': '*',
    },
  });
}

// Blob memo: one materialisation per video, not one per Range request (iOS
// issues several per reel). Tiny LRU; dropped on prune/re-put.
const blobMemo = new Map();
function memoGet(key) {
  const e = blobMemo.get(key);
  if (e) { blobMemo.delete(key); blobMemo.set(key, e); }
  return e;
}
function memoSet(key, entry) {
  blobMemo.set(key, entry);
  while (blobMemo.size > BLOB_MEMO_MAX) blobMemo.delete(blobMemo.keys().next().value);
}
function memoDrop(key) { blobMemo.delete(key); }

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  const isVideo = url.pathname.endsWith('.mp4') || event.request.destination === 'video';

  if (!isVideo) {
    return; // Pass through non-video requests normally
  }

  // Key the cache by URL without query params
  const cacheKey = event.request.url.split('?')[0];

  event.respondWith(
    caches.open(CACHE_NAME).then(async (cache) => {
      const cached = await cache.match(cacheKey);
      const rangeHeader = event.request.headers.get('Range');

      if (cached) {
        if (rangeHeader) {
          let entry = memoGet(cacheKey);
          if (!entry) {
            entry = { blob: await cached.blob(), type: cached.headers.get('Content-Type') || 'video/mp4' };
            memoSet(cacheKey, entry);
          }
          return createPartialBlobResponse(entry.blob, rangeHeader, entry.type);
        }
        return cached;
      }

      // Not in cache: fetch from network
      try {
        const networkResponse = await fetch(event.request);
        return networkResponse;
      } catch (err) {
        return new Response('Video offline unavailable', { status: 503, statusText: 'Service Unavailable' });
      }
    })
  );
});

// ---- Precache: newest window wins, one in-flight fetch per URL, bounded concurrency ----
const inflight = new Map(); // cleanUrl -> Promise<boolean>
let wanted = [];
let draining = false;

function storeResponse(cache, cleanUrl, res) {
  const headers = { 'Content-Type': res.headers.get('Content-Type') || 'video/mp4', 'Accept-Ranges': 'bytes' };
  const len = res.headers.get('Content-Length');
  if (len) headers['Content-Length'] = len;
  memoDrop(cleanUrl);
  // Streamed put: the body goes to CacheStorage without a full in-memory Blob.
  return cache.put(cleanUrl, new Response(res.body, { status: 200, headers }));
}

function cacheOne(cache, rawUrl) {
  const cleanUrl = rawUrl.split('?')[0];
  if (inflight.has(cleanUrl)) return inflight.get(cleanUrl);
  const p = (async () => {
    if (await cache.match(cleanUrl)) return true;
    const res = await fetch(rawUrl, { mode: 'cors' });
    if (!res.ok) return false;
    await storeResponse(cache, cleanUrl, res);
    return true;
  })().catch(() => false).finally(() => inflight.delete(cleanUrl));
  inflight.set(cleanUrl, p);
  return p;
}

async function precacheUrls(urls) {
  wanted = urls.slice(); // the newest window replaces any older wish-list
  if (draining) return;
  draining = true;
  try {
    const cache = await caches.open(CACHE_NAME);
    while (wanted.length) {
      const batch = wanted.splice(0, PRECACHE_CONCURRENCY);
      await Promise.all(batch.map((u) => cacheOne(cache, u)));
    }
  } finally {
    draining = false;
  }
}

async function pruneCache(keepUrls, preventPrune) {
  if (preventPrune) return;
  const cache = await caches.open(CACHE_NAME);
  const keepSet = new Set(keepUrls.map((u) => u.split('?')[0]));
  const requests = await cache.keys();

  for (const req of requests) {
    const reqClean = req.url.split('?')[0];
    if (keepSet.has(reqClean) || inflight.has(reqClean)) continue;
    memoDrop(reqClean);
    await cache.delete(req);
  }
}

self.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || !data.action) return;

  if (data.action === 'PRECACHE_VIDEOS' && Array.isArray(data.urls)) {
    event.waitUntil(precacheUrls(data.urls));
  } else if (data.action === 'PRUNE_CACHE' && Array.isArray(data.keepUrls)) {
    event.waitUntil(pruneCache(data.keepUrls, data.preventPrune));
  } else if (data.action === 'GET_CACHED_URLS') {
    event.waitUntil(
      caches.open(CACHE_NAME).then(async (cache) => {
        const keys = await cache.keys();
        const urls = keys.map((r) => r.url.split('?')[0]);
        if (event.source) {
          event.source.postMessage({ action: 'CACHED_URLS_LIST', urls });
        }
      })
    );
  }
});
