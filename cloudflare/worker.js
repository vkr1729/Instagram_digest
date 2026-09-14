// worker.js — Instagram Digest Bookmarks API (Cloudflare Worker + D1 + R2).
// Frozen contract: see docs/superpowers/plans/...-implementation-plan-revised.md §2.
// Auth: Authorization: Bearer <OWNER_KEY>, X-Owner-Key accepted as fallback.
// Telegram: URL-only sendVideo (never proxy bytes through the Worker).

const MAX_ITEMS = 300;
const MAX_BYTES = 3.5 * 1024 * 1024 * 1024;
const MAX_VIDEO_BYTES = 50 * 1024 * 1024; // Telegram Bot API ceiling
const ORIGIN = 'https://vkr1729.github.io';

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': ORIGIN,
    'Access-Control-Allow-Methods': 'GET, POST, DELETE, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, X-Owner-Key',
    'Vary': 'Origin',
  };
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders() },
  });
}

function authed(request, env) {
  const h = request.headers.get('Authorization') || '';
  const bearer = h.startsWith('Bearer ') ? h.slice(7) : '';
  const legacy = request.headers.get('X-Owner-Key') || '';
  const key = bearer || legacy;
  return Boolean(key) && key === env.OWNER_KEY;
}

async function regenManifest(env) {
  const { results } = await env.DB.prepare(
    'SELECT id, creator_handle, caption, category, thumbnail_url, video_url,' +
    ' size_bytes, bookmarked_at, telegram_message_id FROM bookmarks ORDER BY bookmarked_at ASC'
  ).all();
  await env.MY_BUCKET.put(
    'bookmarks/manifest.json',
    JSON.stringify(results || []),
    { httpMetadata: { contentType: 'application/json', cacheControl: 'no-cache' } }
  );
  return results || [];
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
    // Delete oldest-first until back under cap (bounded per pass).
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
  await Promise.all(ids.flatMap((id) => [
    env.MY_BUCKET.delete(`bookmarks/${id}.mp4`).catch(() => {}),
    env.MY_BUCKET.delete(`bookmarks/${id}_portrait.jpg`).catch(() => {}),
  ]));
}

async function sendTelegram(env, id, videoUrl, caption, sizeBytes = 0) {
  // 1. Fast path: URL download for videos <= 20 MB (Telegram Bot API limit for external URL fetching)
  if (sizeBytes && sizeBytes <= 20 * 1024 * 1024) {
    try {
      const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendVideo`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: env.TELEGRAM_CHAT_ID,
          video: videoUrl,
          caption: (caption || '').slice(0, 1000),
          supports_streaming: true,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        if (data?.result?.message_id) return data.result.message_id;
      }
    } catch {}
  }

  // 2. Large videos (> 20 MB up to 50 MB) or fallback: upload directly via multipart/form-data
  if (sizeBytes <= MAX_VIDEO_BYTES) {
    try {
      const srcObj = await env.MY_BUCKET.get(`bookmarks/${id}.mp4`);
      if (srcObj) {
        const blob = await srcObj.blob();
        const form = new FormData();
        form.append('chat_id', env.TELEGRAM_CHAT_ID);
        form.append('video', blob, `${id}.mp4`);
        form.append('caption', (caption || '').slice(0, 1000));
        form.append('supports_streaming', 'true');

        const res = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendVideo`, {
          method: 'POST',
          body: form,
        });
        if (res.ok) {
          const data = await res.json();
          return data?.result?.message_id ?? null;
        }
      }
    } catch {}
  }

  return null;
}

async function handlePost(request, env) {
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: 'BAD_JSON' }, 400);
  }
  const { id, creator_handle, caption, category, video_url, thumbnail_url } = body || {};
  if (!id || !video_url) return json({ error: 'MISSING_FIELDS' }, 400);
  if (!video_url.startsWith(env.R2_PUBLIC_BASE_URL)) return json({ error: 'UNTRUSTED_SOURCE' }, 400);

  const sourceKey = video_url.slice(env.R2_PUBLIC_BASE_URL.length).replace(/^\//, '');
  if (!sourceKey.startsWith('videos/')) return json({ error: 'UNTRUSTED_SOURCE' }, 400);

  const src = await env.MY_BUCKET.get(sourceKey);
  if (!src) return json({ error: 'SOURCE_PURGED' }, 404);
  const size = src.size; // authoritative — never trust client size_bytes
  if (size > MAX_VIDEO_BYTES) return json({ error: 'TOO_LARGE' }, 413);

  await env.MY_BUCKET.put(`bookmarks/${id}.mp4`, src.body, {
    httpMetadata: { contentType: 'video/mp4', cacheControl: 'public, max-age=31536000, immutable' },
  });

  // Thumbnails live on Pages, not R2: fetch over HTTPS, fail soft.
  let thumbUrl = '';
  if (thumbnail_url && thumbnail_url.startsWith('https://')) {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 10000);
      const tr = await fetch(thumbnail_url, { signal: ctl.signal });
      clearTimeout(t);
      if (tr.ok) {
        await env.MY_BUCKET.put(`bookmarks/${id}_portrait.jpg`, tr.body, {
          httpMetadata: { contentType: 'image/jpeg', cacheControl: 'public, max-age=31536000, immutable' },
        });
        thumbUrl = `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}_portrait.jpg`;
      }
    } catch { /* thumbnail failure never fails the bookmark */ }
  }

  const now = new Date().toISOString();
  await env.DB.batch([
    env.DB.prepare(
      'INSERT OR IGNORE INTO bookmarks (id, creator_handle, caption, category, thumbnail_url,' +
      ' video_url, size_bytes, bookmarked_at) VALUES (?,?,?,?,?,?,?,?)'
    ).bind(
      id, creator_handle || '', caption || '', category || '', thumbUrl,
      `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}.mp4`, size, now
    ),
  ]);
  const evicted = await enforceCap(env);
  if (evicted.length) await deleteR2Keys(env, evicted);
  await regenManifest(env);

  // Best-effort cold archive; bookmark succeeds regardless.
  const tgId = await sendTelegram(env, id, `${env.R2_PUBLIC_BASE_URL}/bookmarks/${id}.mp4`, caption || '', size);
  if (tgId !== null) {
    await env.DB.prepare('UPDATE bookmarks SET telegram_message_id = ? WHERE id = ?').bind(tgId, id).run();
    await regenManifest(env);
  }
  return json({ ok: true, id, size_bytes: size, telegram_message_id: tgId, evicted });
}

async function handleDelete(id, env) {
  if (!id) return json({ error: 'MISSING_ID' }, 400);
  await env.DB.prepare('DELETE FROM bookmarks WHERE id = ?').bind(id).run();
  await deleteR2Keys(env, [id]);
  await regenManifest(env);
  return json({ ok: true, id }); // idempotent; Telegram copy retained permanently
}

async function handleGet(env) {
  const rows = await regenManifest(env);
  return json(rows);
}

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/$/, '') || '/';

    if (path === '/api/bookmarks' && request.method === 'GET') {
      if (!authed(request, env)) return json({ error: 'Forbidden' }, 403);
      return handleGet(env);
    }
    if (path === '/api/bookmark' && request.method === 'POST') {
      if (!authed(request, env)) return json({ error: 'Forbidden' }, 403);
      return handlePost(request, env);
    }
    const del = path.match(/^\/api\/bookmark\/([^/]+)$/);
    if (del && request.method === 'DELETE') {
      if (!authed(request, env)) return json({ error: 'Forbidden' }, 403);
      return handleDelete(decodeURIComponent(del[1]), env);
    }
    return json({ error: 'NOT_FOUND' }, 404);
  },
};
