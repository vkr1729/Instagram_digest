-- schema.sql — Cloudflare D1 SQLite schema for Hybrid Bookmarks.
-- size_bytes stores VIDEO bytes only (thumbnails ~100 KB are excluded by
-- definition so cap accounting stays stable; see worker.js).

CREATE TABLE IF NOT EXISTS bookmarks (
  id TEXT PRIMARY KEY,
  creator_handle TEXT NOT NULL,
  caption TEXT DEFAULT '',
  category TEXT DEFAULT '',
  thumbnail_url TEXT DEFAULT '',
  video_url TEXT NOT NULL,
  size_bytes INTEGER NOT NULL,
  bookmarked_at TEXT NOT NULL,
  telegram_message_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_bookmarks_at ON bookmarks (bookmarked_at);
