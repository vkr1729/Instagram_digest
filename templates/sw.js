// sw.js — Instagram Digest Service Worker
// Provides persistent video caching with zero-copy HTTP 206 Partial Content range slicing for iOS WebKit

const CACHE_NAME = 'ig-digest-media-v1';

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

// Helper to construct a synthetic 206 Partial Content response using zero-copy Blob.slice
function createPartialBlobResponse(blob, rangeHeader, contentType) {
  const total = blob.size;
  const matches = rangeHeader ? rangeHeader.match(/bytes=(\d+)-(\d+)?/) : null;

  let start = 0;
  let end = total - 1;

  if (matches) {
    start = parseInt(matches[1], 10);
    if (matches[2]) {
      end = parseInt(matches[2], 10);
    }
  }

  // Bound check
  start = Math.max(0, Math.min(start, total - 1));
  end = Math.max(start, Math.min(end, total - 1));
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
          const blob = await cached.blob();
          return createPartialBlobResponse(blob, rangeHeader, cached.headers.get('Content-Type') || 'video/mp4');
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

// Background queue management via Client Messages
let isDownloadingAll = false;
let cancelDownloadAll = false;

self.addEventListener('message', (event) => {
  const data = event.data;
  if (!data || !data.action) return;

  if (data.action === 'PRECACHE_VIDEOS' && Array.isArray(data.urls)) {
    event.waitUntil(precacheUrls(data.urls));
  } else if (data.action === 'PRUNE_CACHE' && Array.isArray(data.keepUrls)) {
    event.waitUntil(pruneCache(data.keepUrls, data.preventPrune));
  } else if (data.action === 'DOWNLOAD_ALL' && Array.isArray(data.urls)) {
    cancelDownloadAll = false;
    event.waitUntil(downloadAllVideos(data.urls, event.source));
  } else if (data.action === 'CANCEL_DOWNLOAD_ALL') {
    cancelDownloadAll = true;
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

async function precacheUrls(urls) {
  const cache = await caches.open(CACHE_NAME);
  for (const rawUrl of urls) {
    const cleanUrl = rawUrl.split('?')[0];
    const existing = await cache.match(cleanUrl);
    if (!existing) {
      try {
        const res = await fetch(rawUrl, { mode: 'cors' });
        if (res.ok) {
          const blob = await res.blob();
          const cachedRes = new Response(blob, {
            status: 200,
            headers: {
              'Content-Type': res.headers.get('Content-Type') || 'video/mp4',
              'Content-Length': String(blob.size),
              'Accept-Ranges': 'bytes',
            },
          });
          await cache.put(cleanUrl, cachedRes);
        }
      } catch (e) {
        // Ignore single fetch error, continue
      }
    }
  }
}

async function pruneCache(keepUrls, preventPrune) {
  if (preventPrune) return;
  const cache = await caches.open(CACHE_NAME);
  const keepSet = new Set(keepUrls.map((u) => u.split('?')[0]));
  const requests = await cache.keys();

  for (const req of requests) {
    const reqClean = req.url.split('?')[0];
    if (!keepSet.has(reqClean)) {
      await cache.delete(req);
    }
  }
}

async function downloadAllVideos(urls, client) {
  if (isDownloadingAll) return;
  isDownloadingAll = true;
  cancelDownloadAll = false;
  const cache = await caches.open(CACHE_NAME);
  const total = urls.length;

  // 1. Identify missing URLs (differential check)
  const missingUrls = [];
  let cachedCount = 0;

  for (const rawUrl of urls) {
    const cleanUrl = rawUrl.split('?')[0];
    const existing = await cache.match(cleanUrl);
    if (existing) {
      cachedCount++;
    } else {
      missingUrls.push(rawUrl);
    }
  }

  // Initial progress notification
  if (client) {
    client.postMessage({
      action: 'DOWNLOAD_PROGRESS',
      completed: cachedCount,
      total: total,
      url: '',
    });
  }

  if (missingUrls.length === 0) {
    isDownloadingAll = false;
    if (client) {
      client.postMessage({
        action: 'DOWNLOAD_COMPLETE',
        total: total,
      });
    }
    return;
  }

  // 2. Concurrency pool with 3 parallel workers and 1 network retry
  let currentIndex = 0;
  let newlyDownloaded = 0;

  async function downloadWorker() {
    while (currentIndex < missingUrls.length && !cancelDownloadAll) {
      const idx = currentIndex++;
      const rawUrl = missingUrls[idx];
      const cleanUrl = rawUrl.split('?')[0];

      let success = false;
      for (let attempt = 0; attempt < 2; attempt++) {
        if (cancelDownloadAll) break;
        try {
          const res = await fetch(rawUrl, { mode: 'cors' });
          if (res.ok) {
            const blob = await res.blob();
            const cachedRes = new Response(blob, {
              status: 200,
              headers: {
                'Content-Type': res.headers.get('Content-Type') || 'video/mp4',
                'Content-Length': String(blob.size),
                'Accept-Ranges': 'bytes',
              },
            });
            await cache.put(cleanUrl, cachedRes);
            success = true;
            break;
          }
        } catch (e) {
          if (attempt === 0) {
            await new Promise((r) => setTimeout(r, 600));
          }
        }
      }

      newlyDownloaded++;
      if (client) {
        client.postMessage({
          action: 'DOWNLOAD_PROGRESS',
          completed: cachedCount + newlyDownloaded,
          total: total,
          url: cleanUrl,
        });
      }
    }
  }

  const workers = [downloadWorker(), downloadWorker(), downloadWorker()];
  await Promise.all(workers);

  isDownloadingAll = false;
  if (client) {
    client.postMessage({
      action: 'DOWNLOAD_COMPLETE',
      total: total,
    });
  }
}
