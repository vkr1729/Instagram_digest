// worker.js — Instagram Digest Bookmarks API (Cloudflare Worker + D1 + R2).
// Contract: docs/superpowers/plans/...-implementation-plan-revised.md §2, plus:
//   - replayed POST of an existing id returns {ok:true, duplicate:true} and has NO side effects
//   - telegram_message_id is stamped in the background (ctx.waitUntil); read it via GET
//   - daily cron reconciles bookmarks/* objects against D1 rows
// Auth: Authorization: Bearer <OWNER_KEY>, X-Owner-Key accepted as fallback.
// Telegram: sendVideo by URL (<= 20 MB); larger videos are STREAMED as
// multipart straight from R2 (the Worker never holds the video in memory).

const MAX_ITEMS = 300;
const MAX_BYTES = 3.5 * 1024 * 1024 * 1024;
const MAX_VIDEO_BYTES = 50 * 1024 * 1024;   // Telegram Bot API multipart ceiling
const TG_URL_FETCH_MAX = 20 * 1024 * 1024;  // Telegram fetches at most 20 MB by URL itself
const MAX_THUMB_BYTES = 2 * 1024 * 1024;
const MAX_JSON_BODY = 64 * 1024;
const ID_RE = /^[A-Za-z0-9_-]{1,64}$/;      // Instagram shortcodes; also our R2 key component
const SOURCE_KEY_RE = /^videos\/\d{4}-\d{2}-\d{2}\/[A-Za-z0-9._-]+\.mp4$/;
const DEFAULT_ORIGINS = ['https://vkr1729.github.io', 'http://127.0.0.1:8080', 'http://localhost:8080'];

function allowedOrigins(env) {
  const extra = (env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
  return new Set([...DEFAULT_ORIGINS, ...extra]);
}

function corsHeaders(request, env) {
  const origin = request.headers.get('Origin') || '';
  const allow = allowedOrigins(env).has(origin) ? origin : DEFAULT_ORIGINS[0];
  return {
    'Access-Control-Allow-Origin': allow,
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Owner-Key',
    'Access-Control-Max-Age': '600',
    'Vary': 'Origin',
  };
}

function json(request, env, data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store', ...corsHeaders(request, env) },
  });
}

const str = (v, max) => (typeof v === 'string' ? v.slice(0, max) : '');

async function authed(request, env) {
  const h = request.headers.get('Authorization') || '';
  const bearer = h.startsWith('Bearer ') ? h.slice(7) : '';
  const key = bearer || request.headers.get('X-Owner-Key') || '';
  if (!key || !env.OWNER_KEY) return false;
  // Constant-time: compare fixed-length SHA-256 digests with an explicit
  // XOR loop (no timingSafeEqual dependency), never the raw strings.
  const enc = new TextEncoder();
  const [da, db] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(key)),
    crypto.subtle.digest('SHA-256', enc.encode(env.OWNER_KEY)),
  ]);
  const a = new Uint8Array(da);
  const b = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  const ok = diff === 0;
  if (!ok) console.warn('bookmark API: presented owner key rejected');
  return ok;
}

async function selectAll(env) {
  const { results } = await env.DB.prepare(
    'SELECT id, creator_handle, caption, category, thumbnail_url, video_url,' +
    ' size_bytes, bookmarked_at, telegram_message_id FROM bookmarks ORDER BY bookmarked_at ASC'
  ).all();
  return results || [];
}

// Debug/read-cache artifact on the media host (not read by the PWA).
async function regenManifest(env) {
  const rows = await selectAll(env);
  await env.MY_BUCKET.put(
    'bookmarks/manifest.json',
    JSON.stringify(rows),
    { httpMetadata: { contentType: 'application/json', cacheControl: 'no-cache' } }
  );
  return rows;
}

// Enforce 300-item / 3.5 GB cap. Returns evicted ids. Runs a re-check pass so
// two concurrent requests that each overshoot by a row self-heal.
async function enforceCap(env) {
  const evicted = [];
  for (let pass = 0; pass < 2; pass++) {
    const countRow = await env.DB.prepare(
      'SELECT COUNT(*) AS c, COALESCE(SUM(size_bytes),0) AS s FROM bookmarks'
    ).first();
    const over = (countRow.c - MAX_ITEMS) + (countRow.s > MAX_BYTES ? 1 : 0);
    if (over <= 0) break;
    const { results } = await env.DB.prepare(
      'SELECT id, size_bytes FROM bookmarks ORDER BY bookmarked_at ASC LIMIT 50'
    ).all();
    let c = countRow.c, s = countRow.s;
    const victims = [];
    for (const r of results || []) {
      if (c <= MAX_ITEMS && s <= MAX_BYTES) break;
      victims.push(r.id);
      c -= 1;
      s -= r.size_bytes;
    }
    if (!victims.length) break;
    await env.DB.batch(victims.map((id) => env.DB.prepare('DELETE FROM bookmarks WHERE id = ?').bind(id)));
    evicted.push(...victims);
  }
  return evicted;
}

async function deleteR2Keys(env, ids) {
  const keys = ids.flatMap((id) => [`bookmarks/${id}.mp4`, `bookmarks/${id}_portrait.jpg`]);
  for (let i = 0; i < keys.length; i += 1000) {
    await env.MY_BUCKET.delete(keys.slice(i, i + 1000)).catch(() => {});
  }
}

// Bounded, allow-listed fetch for thumbnails: only the Pages thumbnails prefix,
// at most MAX_THUMB_BYTES, aborted the moment the body exceeds the cap.
async function fetchBounded(url, maxBytes, timeoutMs, allowedPrefix) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { signal: ctl.signal, redirect: 'follow' });
    if (!r.ok || !r.url.startsWith(allowedPrefix)) return null;
    const declared = Number(r.headers.get('Content-Length') || 0);
    if (declared > maxBytes) return null;
    const reader = r.body.getReader();
    const chunks = [];
    let n = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      n += value.byteLength;
      if (n > maxBytes) { ctl.abort(); return null; }
      chunks.push(value);
    }
    const out = new Uint8Array(n);
    let o = 0;
    for (const c of chunks) { out.set(c, o); o += c.byteLength; }
    return out;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function tgSendVideoByUrl(env, videoUrl, caption) {
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendVideo`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, video: videoUrl, caption, supports_streaming: true }),
  });
  if (!res.ok) return null;
  const data = await res.json().catch(() => null);
  return data?.result?.message_id ?? null;
}

// Streams the R2 object straight into a multipart body. FixedLengthStream
// advertises Content-Length so the upload is not chunked. Peak Worker memory
// is one stream buffer, not the video.
async function tgSendVideoMultipart(env, id, caption) {
  const obj = await env.MY_BUCKET.get(`bookmarks/${id}.mp4`);
  if (!obj) return null;
  const boundary = '----IgDigest' + crypto.randomUUID().replace(/-/g, '');
  const enc = new TextEncoder();
  const field = (name, value) =>
    enc.encode(`--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${value}\r\n`);
  const head = [
    field('chat_id', env.TELEGRAM_CHAT_ID),
    field('supports_streaming', 'true'),
    field('caption', caption),
    enc.encode(`--${boundary}\r\nContent-Disposition: form-data; name="video"; filename="${id}.mp4"\r\nContent-Type: video/mp4\r\n\r\n`),
  ];
  const tail = enc.encode(`\r\n--${boundary}--\r\n`);
  const total = head.reduce((n, p) => n + p.byteLength, 0) + obj.size + tail.byteLength;
  const { readable, writable } = new FixedLengthStream(total);
  const pump = (async () => {
    const w = writable.getWriter();
    for (const part of head) await w.write(part);
    w.releaseLock();
    await obj.body.pipeTo(writable, { preventClose: true });
    const w2 = writable.getWriter();
    await w2.write(tail);
    await w2.close();
  })();
  const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendVideo`, {
    method: 'POST',
    headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
    body: readable,
  });
  await pump.catch(() => {});
  if (!res.ok) return null;
  const data = await res.json().catch(() => null);
  return data?.result?.message_id ?? null;
}

// Best-effort cold archive, run AFTER the response via ctx.waitUntil. The
// stamp is guarded (telegram_message_id IS NULL) so a replayed POST can never
// produce a second post; the row is re-checked so an evicted/deleted bookmark
// is not stamped either.
async function archiveToTelegram(env, id, caption, sizeBytes) {
  if (!env.TELEGRAM_BOT_TOKEN || !env.TELEGRAM_CHAT_ID) return;
  const row = await env.DB.prepare('SELECT telegram_message_id FROM bookmarks WHERE id = ?').bind(id).first();
  if (!row || row.telegram_message_id !== null) return;
  const cap = (caption || '').slice(0, 1000);
  let msgId = null;
  try {
    if (sizeBytes <= TG_URL_FETCH_MAX) {
      msgId = await tgSendVideoByUrl(env, `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}.mp4`, cap);
    }
    if (msgId === null && sizeBytes <= MAX_VIDEO_BYTES) {
      msgId = await tgSendVideoMultipart(env, id, cap);
    }
    if (msgId === null) {
      // One delayed retry for transient Telegram/R2 blips. waitUntil allows
      // ~30s; this costs 5s + one more attempt before giving up for good.
      await new Promise((r) => setTimeout(r, 5000));
      // Re-check the guard: the row may have been deleted/evicted meanwhile.
      const still = await env.DB.prepare(
        'SELECT telegram_message_id FROM bookmarks WHERE id = ?').bind(id).first();
      if (still && still.telegram_message_id === null) {
        if (sizeBytes <= TG_URL_FETCH_MAX) {
          msgId = await tgSendVideoByUrl(env, `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}.mp4`, cap);
        }
        if (msgId === null && sizeBytes <= MAX_VIDEO_BYTES) {
          msgId = await tgSendVideoMultipart(env, id, cap);
        }
      }
    }
  } catch {
    msgId = null;
  }
  if (msgId !== null) {
    await env.DB.prepare(
      'UPDATE bookmarks SET telegram_message_id = ? WHERE id = ? AND telegram_message_id IS NULL'
    ).bind(msgId, id).run();
  }
  await regenManifest(env);
}

async function handlePost(request, env, ctx) {
  const declared = Number(request.headers.get('Content-Length') || 0);
  if (declared > MAX_JSON_BODY) return json(request, env, { error: 'BODY_TOO_LARGE' }, 413);
  let body;
  try {
    const buf = await request.arrayBuffer();
    if (buf.byteLength > MAX_JSON_BODY) return json(request, env, { error: 'BODY_TOO_LARGE' }, 413);
    body = JSON.parse(new TextDecoder().decode(buf));
  } catch {
    return json(request, env, { error: 'BAD_JSON' }, 400);
  }
  const id = str(body?.id, 64);
  const video_url = str(body?.video_url, 2048);
  const thumbnail_url = str(body?.thumbnail_url, 2048);
  const creator_handle = str(body?.creator_handle, 64);
  const caption = str(body?.caption, 1000);
  const category = str(body?.category, 32);
  if (!id || !video_url) return json(request, env, { error: 'MISSING_FIELDS' }, 400);
  if (!ID_RE.test(id)) return json(request, env, { error: 'BAD_ID' }, 400);

  // Idempotent replay (outbox retry after a lost response): the row already
  // exists -> return it and touch nothing (no R2 re-copy, no Telegram repost).
  const existing = await env.DB.prepare(
    'SELECT size_bytes, telegram_message_id FROM bookmarks WHERE id = ?'
  ).bind(id).first();
  if (existing) {
    return json(request, env, {
      ok: true, id, size_bytes: existing.size_bytes,
      telegram_message_id: existing.telegram_message_id ?? null, evicted: [], duplicate: true,
    });
  }

  const base = env.R2_PUBLIC_BASE_URL.replace(/\/$/, '') + '/';
  if (!video_url.startsWith(base)) return json(request, env, { error: 'UNTRUSTED_SOURCE' }, 400);
  let sourceKey;
  try {
    sourceKey = decodeURIComponent(video_url.slice(base.length).split('?')[0]);
  } catch {
    return json(request, env, { error: 'UNTRUSTED_SOURCE' }, 400);
  }
  if (!SOURCE_KEY_RE.test(sourceKey)) return json(request, env, { error: 'UNTRUSTED_SOURCE' }, 400);
  if (!sourceKey.endsWith(`_${id}.mp4`)) return json(request, env, { error: 'ID_SOURCE_MISMATCH' }, 400);

  const src = await env.MY_BUCKET.get(sourceKey);
  if (!src) return json(request, env, { error: 'SOURCE_PURGED' }, 404);
  const size = src.size; // authoritative — never trust client size_bytes
  if (size > MAX_VIDEO_BYTES) return json(request, env, { error: 'TOO_LARGE' }, 413);

  await env.MY_BUCKET.put(`bookmarks/${id}.mp4`, src.body, {
    httpMetadata: { contentType: 'video/mp4', cacheControl: 'public, max-age=31536000, immutable' },
  });

  // Thumbnails live on Pages: fetch only from that prefix, bounded, fail soft.
  let thumbUrl = '';
  const thumbBase = (env.THUMB_BASE_URL || '').replace(/\/$/, '') + '/';
  if (thumbBase.length > 1 && thumbnail_url.startsWith(thumbBase)) {
    const bytes = await fetchBounded(thumbnail_url, MAX_THUMB_BYTES, 10000, thumbBase);
    if (bytes) {
      await env.MY_BUCKET.put(`bookmarks/${id}_portrait.jpg`, bytes, {
        httpMetadata: { contentType: 'image/jpeg', cacheControl: 'public, max-age=31536000, immutable' },
      });
      thumbUrl = `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}_portrait.jpg`;
    }
  }

  const now = new Date().toISOString();
  const ins = await env.DB.prepare(
    'INSERT OR IGNORE INTO bookmarks (id, creator_handle, caption, category, thumbnail_url,' +
    ' video_url, size_bytes, bookmarked_at) VALUES (?,?,?,?,?,?,?,?)'
  ).bind(
    id, creator_handle, caption, category, thumbUrl,
    `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}.mp4`, size, now
  ).run();
  const inserted = (ins?.meta?.changes ?? 1) > 0; // 0 -> a concurrent POST won the race
  const evicted = await enforceCap(env);
  if (evicted.length) await deleteR2Keys(env, evicted);
  await regenManifest(env);
  if (inserted) ctx.waitUntil(archiveToTelegram(env, id, caption, size));
  return json(request, env, { ok: true, id, size_bytes: size, telegram_message_id: null, evicted, duplicate: !inserted });
}

async function handleDelete(request, env, rawId) {
  let id;
  try {
    id = decodeURIComponent(rawId);
  } catch {
    return json(request, env, { error: 'BAD_ID' }, 400);
  }
  if (!ID_RE.test(id)) return json(request, env, { error: 'BAD_ID' }, 400);
  await env.DB.prepare('DELETE FROM bookmarks WHERE id = ?').bind(id).run();
  await deleteR2Keys(env, [id]);
  await regenManifest(env);
  return json(request, env, { ok: true, id }); // idempotent; Telegram copy retained permanently
}

async function handleGet(request, env) {
  return json(request, env, await selectAll(env)); // read-only: no R2 write per read
}

// Daily reconcile: objects without rows (failed deletes, POST/DELETE races) are
// removed; rows whose video vanished are dead bookmarks and are dropped.
async function reconcileOrphans(env) {
  const rows = await selectAll(env);
  const live = new Set(rows.map((r) => r.id));
  const orphans = [];
  let cursor;
  do {
    const page = await env.MY_BUCKET.list({ prefix: 'bookmarks/', cursor });
    for (const o of page.objects) {
      if (o.key === 'bookmarks/manifest.json') continue;
      const m = o.key.match(/^bookmarks\/([A-Za-z0-9_-]+?)(?:_portrait\.jpg|\.mp4)$/);
      if (!m || !live.has(m[1])) orphans.push(o.key);
    }
    cursor = page.truncated ? page.cursor : undefined;
  } while (cursor);
  for (let i = 0; i < orphans.length; i += 1000) {
    await env.MY_BUCKET.delete(orphans.slice(i, i + 1000)).catch(() => {});
  }
  const dead = [];
  const ids = [...live];
  for (let i = 0; i < ids.length; i += 20) {
    const batch = ids.slice(i, i + 20);
    const heads = await Promise.all(batch.map((id) => env.MY_BUCKET.head(`bookmarks/${id}.mp4`)));
    heads.forEach((h, j) => { if (!h) dead.push(batch[j]); });
  }
  if (dead.length) {
    await env.DB.batch(dead.map((id) => env.DB.prepare('DELETE FROM bookmarks WHERE id = ?').bind(id)));
  }
  if (orphans.length || dead.length) await regenManifest(env);
  return { orphans: orphans.length, dead: dead.length };
}

export default {
  async fetch(request, env, ctx) {
    try {
      if (request.method === 'OPTIONS') {
        return new Response(null, { status: 204, headers: corsHeaders(request, env) });
      }
      const url = new URL(request.url);
      const path = url.pathname.replace(/\/$/, '') || '/';

      if (path === '/api/bookmarks' && request.method === 'GET') {
        if (!(await authed(request, env))) return json(request, env, { error: 'Forbidden' }, 403);
        return handleGet(request, env);
      }
      if (path === '/api/bookmark' && request.method === 'POST') {
        if (!(await authed(request, env))) return json(request, env, { error: 'Forbidden' }, 403);
        return handlePost(request, env, ctx);
      }
      const del = path.match(/^\/api\/bookmark\/([^/]+)$/);
      if (del && request.method === 'DELETE') {
        if (!(await authed(request, env))) return json(request, env, { error: 'Forbidden' }, 403);
        return handleDelete(request, env, del[1]);
      }
      return json(request, env, { error: 'NOT_FOUND' }, 404);
    } catch (err) {
      // Always answer with CORS headers so the PWA sees a status, not a CORS error.
      return json(request, env, { error: 'INTERNAL', detail: String(err && err.message || err).slice(0, 200) }, 500);
    }
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(reconcileOrphans(env));
  },
};
