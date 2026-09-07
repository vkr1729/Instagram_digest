# Instagram Digest — Architectural & Runtime Review and Fix Plan

**Repository:** `vkr1729/Instagram_digest` @ `af07462` (main), `gh-pages` @ `328e606` (2026-09-07)
**Review target:** iPhone 16 / iOS 26+ standalone PWA (GitHub Pages + Cloudflare R2 via Worker) and the Ubuntu weekly pipeline.
**Evidence base:** full read of every `.py`, `viewer.html`, shell scripts, and the live `gh-pages` tree and `data.json`; live HTTP probe of the R2 Worker.

---

## 1. Executive Health Score

| Area | Score | One-line verdict |
| --- | --- | --- |
| Pipeline resilience (scrape → rank → download → upload → deploy) | **4 / 10** | Silent degradation is the default. A blocked scrape or a failed download never aborts; it ships a broken or empty digest and force-pushes it over the working one. |
| Data integrity of the ranking | **3 / 10** | Verified on the live `data.json`: all 200 items have `like_count = 8% of views`, `timestamp` within one hour of each other, `duration = 30`, `caption = "Reel by @…"`. The "Bayesian engagement" and "recency" terms are constants. The ranker is effectively `views ÷ creator-median`. |
| Storage & cost guard (R2 quota, purge) | **8 / 10** | Preflight quota check and rolling purge are correct and were verified against the live worker (Range + immutable cache headers OK). Local `site/` grows unbounded. |
| PWA playback engine (iOS WebKit) | **6 / 10** | Sliding window teardown is done correctly (`removeAttribute('src'); load()`), but auto-advance is a timer choreography that races WebKit scroll-snap, there is no dead-video handling, and every cold open downloads ~13 MB of posters. |
| Deployment | **7 / 10** | Orphan force-push is the right shape. Share pages and thumbnails accumulate forever and outlive the 7-day video purge. |
| **Overall** | **5.5 / 10** | Works on the happy path; brittle the moment Instagram, R2, or WebKit misbehaves. |

**Top three things to fix first:** (1) a minimum-viability gate before `save_digest_batch` and deploy, (2) drop items whose download/upload failed and add a dead-video watchdog in the viewer, (3) replace the auto-advance timer choreography with a generation-counter + instant scroll.

---

## 2. Critical Bugs & Architectural Flaws

Severity legend: **S1** = corrupts/destroys the weekly deliverable or halts hands-free playback; **S2** = major degradation or intermittent failure; **S3** = real bug, contained blast radius.

### C1 · S1 · A blocked or throttled scrape publishes an empty/garbage digest and force-pushes it over the good one

**ELI15.** The scraper asks Instagram for ~120 creator pages in a row with a 0.2 s pause. Instagram sometimes answers with a login wall or a "challenge" page instead of the reels grid. The code treats that as "this creator has zero reels" and moves on. Nothing checks "did I actually get a sensible number of reels?" before writing `top100_digest.json`, building the site, and running `git push -f`. So a bad Friday silently overwrites last week's working site with a nearly empty one, and the 12-hour `candidates_cache.json` then makes the immediate rerun reuse the same garbage.

**Root cause.**
- `extractor.discover_creator_reel_urls()` swallows every exception and returns `[]` (`extractor.py:425-426`); `page.wait_for_selector` timeout is also swallowed (`:378-381`).
- `main.run_full_sync()` has no viability gate between extraction and `save_digest_batch` (`main.py:98-118`), and writes `candidates_cache.json` regardless of quality (`:99-102`).
- `if not ranked_reels: ... ranked_reels = candidates[:N]` (`main.py:112-115`) actively papers over an empty ranking.
- Deploy is unconditional when `--deploy` is passed (`main.py:178-179`).

**Real-world trigger.** Any of: Instagram soft-block after burst navigation, expired `cookies.json`, Chrome cookie export failing in cron (`run_weekly.sh` continues with `|| true`), Instagram DOM change so `a[href*='/reel/']` matches nothing. Symptom on the iPhone: PWA opens to "You're All Caught Up!" or a 12-reel feed.

**Surgical remediation.**

`extractor.py` — make a block detectable and fatal for the run:
```python
class InstagramBlocked(RuntimeError):
    """Instagram served a login/challenge wall instead of content."""

_BLOCK_MARKERS = ("/accounts/login", "/challenge/", "/accounts/suspended")

def _assert_not_blocked(page, context: str) -> None:
    url = page.url or ""
    if any(m in url for m in _BLOCK_MARKERS):
        raise InstagramBlocked(f"{context}: redirected to {url}")

# in discover_creator_reel_urls(), right after page.goto(...):
        page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        _assert_not_blocked(page, f"@{clean_handle}")
# and change the catch-all so the block propagates:
    except InstagramBlocked:
        raise
    except Exception as exc:
        logger.warning("Playwright reel link discovery exception for @%s: %s", clean_handle, exc)
```

`main.py` — viability gate + cache hygiene + deploy gate:
```python
MIN_CANDIDATE_RATIO = 0.5      # fraction of the expected candidate count
MAX_EMPTY_CREATOR_RATIO = 0.6  # fraction of creators that returned 0 reels
MIN_DEPLOY_ITEMS = int(config.TOP_DIGEST_COUNT * 0.6)

        if not candidates:
            expected = 0
            empty_creators = 0
            try:
                for idx, src in enumerate(ordered_sources, 1):
                    ...
                    reels = extractor.extract_creator_reels(...)
                    expected += max_candidate_reels
                    if not reels:
                        empty_creators += 1
                    candidates.extend(reels)
                    time.sleep(random.uniform(1.2, 2.8))   # see C5
            except extractor.InstagramBlocked as exc:
                logger.error("Instagram blocked the session (%s). Aborting run without touching digest/site.", exc)
                candidates_cache_file.unlink(missing_ok=True)
                return 2

            if (len(candidates) < MIN_CANDIDATE_RATIO * expected
                    or empty_creators > MAX_EMPTY_CREATOR_RATIO * len(ordered_sources)):
                logger.error("Viability gate failed: %d candidates (expected ≥%d), %d/%d creators empty. Aborting.",
                             len(candidates), int(MIN_CANDIDATE_RATIO * expected), empty_creators, len(ordered_sources))
                candidates_cache_file.unlink(missing_ok=True)
                return 2
            candidates_cache_file.write_text(json.dumps(candidates, indent=2), encoding="utf-8")
```
Delete the `ranked_reels = candidates[:N]` fallback (`main.py:114-115`). Before deploy:
```python
    if deploy and not dry_run:
        if len(ranked_reels) < MIN_DEPLOY_ITEMS:
            logger.error("Only %d playable reels; refusing to deploy over the previous digest.", len(ranked_reels))
            return 2
        site_builder.deploy_to_gh_pages()
```
`run_weekly.sh`: treat exit code 2 as "kept previous digest" and do not print "completed successfully".

---

### C2 · S1 · Failed downloads/uploads still get a card, and a dead card halts hands-free playback on iOS

**ELI15.** When a reel's MP4 fails to download, the code `continue`s but leaves the reel in `ranked_reels`. The site builder then invents an R2 URL for it (`f"{R2_PUBLIC_DOMAIN}/videos/{week}/{rank}_{handle}_{id}.mp4"`) that points at nothing. When an R2 upload fails, the uploader returns a **local** path (`/videos/…`) that gets baked into the GitHub Pages build, which resolves to `https://vkr1729.github.io/videos/…` → 404. In the viewer, a 404 video never fires `timeupdate` or `ended`, and there is no `error` listener, so the auto-advance chain stops on a black card forever. The user has to swipe manually, and the 35 % watched logic never marks it, so it comes back next session.

**Root cause.** `main.py:158-160` (`continue` without removing the item), `storage_r2.upload_reel_to_r2()` returning a local path on remote failure (`storage_r2.py:207-208`), `site_builder.build_site()` synthesizing URLs for unknown ids (`site_builder.py:59`), `viewer.html` having no `error`/`stalled` handling (`:1657-1705`).

**Real-world trigger.** Expired `scontent` CDN URL (they carry an `oe=` expiry; the URL was captured during discovery and used up to an hour later), yt-dlp fallback failing on an age-gated reel, R2 5xx during upload, Instagram returning a 20 KB HTML "error" body that fails the 50 KB size check.

**Surgical remediation.**

`storage_r2.py` — never return a local path when R2 is configured:
```python
        except Exception as exc:
            logger.error("Failed uploading to R2: %s", exc)
            return ""          # caller decides; do not leak local paths into the R2 build
    return f"/videos/{week_id}/{key_name}" if not s3 else ""
```

`main.py` — prune to what is actually playable, keep original rank numbers (gaps are fine; the viewer counter uses DOM index):
```python
                public_url = storage_r2.upload_reel_to_r2(local_video_path, week_id=week_id, key_name=filename)
                if not public_url:
                    logger.warning("Upload failed for %s; dropping from digest.", reel_id)
                    continue
                uploaded_url_map[reel_id] = public_url

            dropped = [r["id"] for r in ranked_reels if r["id"] not in uploaded_url_map]
            if dropped:
                logger.warning("Dropping %d unplayable reels: %s", len(dropped), ", ".join(dropped))
                ranked_reels = [r for r in ranked_reels if r["id"] in uploaded_url_map]
            ranker.save_digest_batch(ranked_reels, run_date=week_id)   # move the save here (also fixes C3b)
```

`site_builder.py` — refuse to synthesize a URL for an unknown id when a URL map was provided:
```python
        if r2_uploaded_urls is not None and item["id"] not in url_map:
            continue   # never render a card we cannot play
        r2_url = url_map.get(item["id"]) or f"{config.R2_PUBLIC_DOMAIN}/videos/{week_id}/{video_filename}"
```

`viewer.html` — dead-video watchdog (pairs with P2 below; the code is in P2).

---

### C3 · S1 · The ranking runs on fabricated metrics, and the persisted digest never receives the real ones

**ELI15.** "Fast mode" discovery (`main.py:92`, `extractor.py:625-639`) fills every reel with made-up numbers: likes = 8 % of views, comments = 0.5 % of views, `timestamp = now`, `duration = 30`, caption = "Reel by @handle". The ranker then computes an "engagement rate" that is identical for every reel and a "recency bonus" that is identical for every reel, so those two terms cancel out. Worse, `timestamp = now` bypasses the `days_back` filter entirely, so a pinned reel from 2023 competes as if it were posted an hour ago. Real metadata is only fetched *while downloading*, only when the file isn't cached yet, and *after* `save_digest_batch` already wrote the JSON — which is why the live `data.json` has 200/200 generic captions and a single like/view ratio (0.08).

**Verified on live data (`gh-pages/data.json`, 2026-09-06):** distinct `like/view` ratios = 1 (`0.08`); all timestamps within 3 600 s; all durations `30`; 200/200 captions start with `Reel by @`.

**Root cause.** Ordering in `run_full_sync`: rank → save → (maybe) enrich → download. Fabricated fields are indistinguishable from real ones downstream.

**Real-world trigger.** Every run. Symptoms the user sees: pinned/evergreen reels in the feed, captions that are all "Reel by @x", view counts that don't match engagement, `30 s` duration hints being wrong everywhere.

**Surgical remediation.**

1. Mark synthetic values so nothing downstream trusts them (`extractor.py`, fast-mode branch):
```python
            results.append({
                "id": info["id"], "url": info["url"], "creator_handle": clean_handle,
                "caption": "", "view_count": info.get("view_count", 0),
                "like_count": 0, "comment_count": 0, "duration": 0,
                "timestamp": 0,                       # unknown, not "now"
                "thumbnail": info.get("thumbnail", ""),
                "video_cdn_url": "", "metrics_estimated": True,
            })
```
2. Make the ranker honest about unknowns (`ranker.py:compute_viral_score`):
```python
    if reel.get("metrics_estimated"):
        smooth_engagement = 0.0          # no engagement term without real likes/comments
    else:
        smooth_engagement = (likes + comments * 2.0 + 5.0) / (views + 100.0)
    score = damped_reach * (1.0 + smooth_engagement * 4.0)
    ts = reel.get("timestamp") or 0
    if ts:  # unchanged: only apply recency when the timestamp is real
```
3. Two-pass selection in `main.py`: cheap pass → enrich a bounded shortlist → real pass:
```python
        # Pass 1: cheap reach-only ranking to a shortlist (2x the digest size)
        shortlist = ranker.rank_top_reels(candidates, active_sources,
                                          top_n=config.TOP_DIGEST_COUNT * 2,
                                          max_per_creator=config.MAX_PER_CREATOR + 2, shuffle=False)
        # Enrich the shortlist with real metadata (≈ 3 s/reel, ~20 min for 400)
        cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=days_back)).timestamp())
        enriched = []
        for reel in shortlist:
            meta = extractor.extract_single_reel_metadata(reel, session=session)
            if not meta or meta.get("timestamp", 0) < cutoff_ts:
                continue                      # too old / pinned / unknown date
            meta["metrics_estimated"] = meta.get("like_count", 0) == int(meta.get("view_count", 0) * 0.08)
            enriched.append(meta)
        # Pass 2: the real ranking on real numbers
        ranked_reels = ranker.rank_top_reels(enriched, active_sources,
                                             top_n=config.TOP_DIGEST_COUNT,
                                             max_per_creator=config.MAX_PER_CREATOR)
```
   Remove the caption-enrichment block from the download loop (`main.py:144-150`) and move `save_digest_batch` to after the upload loop (shown in C2).
4. Note on `extract_single_reel_metadata`: the Playwright branch also fabricates `like_count = views*0.08` when the `og:description` regex misses (`extractor.py:474-475`). Set `metrics_estimated = True` there instead of inventing numbers, so step 3's detection isn't needed.

---

### C4 · S2 · One headless Chromium page is reused for ~700 navigations and kept alive through the whole download phase

**ELI15.** Playwright opens one browser tab and navigates it to 120 creator pages, then (after C3) 400 reel pages, then keeps the browser open while 200 MP4s download over `requests`. Chromium's renderer keeps growing with every Instagram page (each is a heavy SPA with megabytes of JS state); a tab that has visited 500 Instagram pages routinely sits at 1.5–2.5 GB RSS. On an Ubuntu box that also runs Chrome for cookies, this ends in swap thrash or the OOM killer taking the run down mid-way, with no checkpoint.

**Root cause.** `InstagramSession` never recycles its context (`extractor.py:295-351`); `with extractor.InstagramSession() as session:` spans steps 3–6 in `main.py:69-169`.

**Surgical remediation.** Recycle the context every N navigations and end the session before downloads:
```python
class InstagramSession:
    RECYCLE_EVERY = 40
    def __init__(self):
        ...; self._nav_count = 0

    def _open_context(self):
        self._context = self._browser.new_context(user_agent=DEFAULT_USER_AGENT)
        self._inject_cookies()               # move cookie injection into a helper
        self._page = self._context.new_page()
        self._nav_count = 0

    def get_page(self):
        self.start()
        if self._nav_count >= self.RECYCLE_EVERY:
            logger.info("Recycling Playwright context after %d navigations.", self._nav_count)
            try:
                self._page.close(); self._context.close()
            except Exception:
                pass
            self._open_context()
        self._nav_count += 1
        return self._page
```
In `main.py`, close the `with` block after enrichment (C3 step 3) and run the download loop outside it. `download_reel_video()` already works from `video_cdn_url` via `requests`; for the rare reel with no CDN URL, let it fall through to `yt-dlp` rather than re-opening Playwright.

---

### C5 · S2 · Burst navigation with a fixed 0.2 s delay is what causes C1

**ELI15.** 120 profile loads in ~2 minutes from one IP with one session looks exactly like a bot. Instagram's response is a login/challenge wall, which C1 then misreads as "no content".

**Root cause.** `time.sleep(0.2)` in `main.py:96`; no jitter; no backoff on empty results.

**Remediation.** Jittered delay (`random.uniform(1.2, 2.8)`, already shown in C1), and exponential backoff when two consecutive creators return zero reels:
```python
                    if not reels:
                        empty_streak += 1
                        if empty_streak >= 2:
                            pause = min(120, 10 * 2 ** (empty_streak - 2))
                            logger.warning("Two empty creators in a row; backing off %ds.", pause)
                            time.sleep(pause)
                    else:
                        empty_streak = 0
```
Adds ~4 minutes to the run and removes the most common cause of a ruined Friday.

---

### C6 · S2 · Share pages, thumbnails and archives are never pruned and outlive the 7-day video purge

**ELI15.** Every week adds ~200 share pages and ~200 thumbnails to `site/` and they are all redeployed every week. After the R2 purge (7 days by default), every share link you sent to a friend renders an OG card and a `<video>` that 404s, with no message. Locally, `site/` grows ~13 MB/week forever. (Verified: `gh-pages` currently has 408 files / 14.8 MB with one week of content; that number only goes up.)

**Root cause.** `site_builder.build_site()` only adds to `site/share`, `site/thumbnails`, `site/archive` (`site_builder.py:93-129, 183-240`); no pruning step exists.

**Remediation.** Add to `build_site()` before rendering:
```python
def _prune_site_assets(current_ids: set[str], keep_week_ids: set[str]) -> None:
    for f in (config.SITE_DIR / "share").glob("*.html"):
        if f.stem not in current_ids: f.unlink(missing_ok=True)
    for f in (config.SITE_DIR / "thumbnails").glob("*.jpg"):
        if f.stem not in current_ids: f.unlink(missing_ok=True)
    for f in (config.SITE_DIR / "archive").glob("*.html"):
        wk = f.stem.replace("local_", "")
        if wk not in keep_week_ids: f.unlink(missing_ok=True)
```
Call with `current_ids = {i["id"] for i in raw_items}` and `keep_week_ids = set(sorted_weeks)`. In the share page, add a one-liner so an expired link says so instead of showing a black box:
```html
<video ... onerror="this.outerHTML='<p style=\'padding:40px;color:#999\'>This reel has expired from the weekly digest.</p>'"></video>
```

---

### C7 · S3 · Local server: unlocked read-modify-write on `watched.json`, `blacklist.json`, `sources.json`

**ELI15.** The server is multi-threaded (`ThreadingHTTPServer`). The viewer fires one `POST /api/watched` per reel (every ~10 s at 1.5x) and a `POST /api/watched/bulk` on "Jump". Two requests can read the same file, each add its own id, and the second write erases the first's id. `unselectCreator` rewrites `sources.json` while a `main.py --sync` may be reading it. Writes are also non-atomic (`write_text` truncates first), so a crash mid-write leaves an empty JSON that every reader then silently treats as `{}`.

**Root cause.** `local_server.py:206-403`, no lock, `Path.write_text` directly.

**Remediation.** One module-level lock and an atomic writer:
```python
import threading, os
_STATE_LOCK = threading.Lock()

def _atomic_write_json(path: Path, data) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
```
Wrap each POST handler's read→mutate→write in `with _STATE_LOCK:` and replace every `config.X_FILE.write_text(...)` with `_atomic_write_json(config.X_FILE, ...)`. Also fix `do_HEAD` (it currently sends a body): `def do_HEAD(self): self.send_response(200); self.end_headers()` is enough for this server.

---

### C8 · S3 · Retention window and archive selector disagree; `RETENTION_DAYS=7` purges last week's videos while its HTML stays deployable

`config.RETENTION_WEEKS=1` → `RETENTION_DAYS=7`. The purge deletes R2 keys whose date is `< now - 7d`; a run at 09:00 on Friday deletes last Friday's keys (dated 00:00). `build_site` keeps `sorted_weeks[:RETENTION_WEEKS]` = 1 week, so the "Prev" option never appears — consistent by accident. Set `RETENTION_DAYS` default to `RETENTION_WEEKS * 7 + 1` so the boundary run doesn't delete the week you may still be watching, and make `build_weeks_metadata` only list weeks whose R2 keys still exist (cheap: `s3.list_objects_v2(Prefix=f"videos/{w}/", MaxKeys=1)`).

---

## 3. PWA (iOS 26 WebKit) Bugs — `templates/viewer.html`

### P1 · S1 · Auto-advance is a timer choreography that races WebKit scroll-snap

**ELI15.** When a reel ends: wait 350 ms → turn *off* scroll-snap → start a *smooth* scroll to the next card → after 280 ms turn snap back *on* and start playing the next video. On an iPhone, a smooth one-viewport scroll inside a snap container takes 350–600 ms. Turning `scroll-snap-type: y mandatory` back on while the scroll is still travelling makes WebKit re-snap to the *nearest* snap point, which is often the card you were leaving. Now the next video's audio is playing while the old card is on screen; 80 ms later `checkCenteredCard()` sees the old card centred and calls `playCardVideo(oldCard)`, which restarts it from 0 (a video that has `ended` restarts on `play()`). Net effect: the same reel replays, or the feed flickers between two cards and the audio jumps. Separately, `handleVideoEnd`'s 350 ms timer fires `advanceToNextReel(oldCard)` even if the user has already swiped elsewhere, yanking them back.

**Root cause.** `viewer.html:1643-1654, 1708-1735, 2020-2055`. Three independent timers (350 ms, 280 ms, 80 ms) plus an IntersectionObserver, with a single boolean `isScrollingTransition` as the only coordination.

**Real-world trigger.** iPhone on 1.5x with hands-free playback; more likely on longer reels (scroll distance is constant, but the smooth-scroll duration varies with device thermal state and Low Power Mode).

**Surgical remediation.** Replace the choreography with a generation counter and an instant scroll. Snap does the positioning; nothing needs to be re-enabled.
```js
let navGen = 0;                       // bumps on every navigation intent

function visibleCards() {
  return Array.from(document.querySelectorAll('.reel-card')).filter(c => c.style.display !== 'none');
}

function goToCard(card) {
  if (!card) return;
  const gen = ++navGen;
  isScrollingTransition = true;
  card.scrollIntoView({ behavior: 'instant', block: 'start' });
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (gen !== navGen) return;       // superseded by a newer intent
    isScrollingTransition = false;
    playCardVideo(card);
  }));
}

function advanceToNextReel(currentCard) {
  const cards = visibleCards();
  const i = cards.indexOf(currentCard);
  if (i === -1 || i + 1 >= cards.length) { filterCategory(currentCategory); return; }
  goToCard(cards[i + 1]);
}

function advanceToPreviousReel(currentCard) {
  const cards = visibleCards();
  const i = cards.indexOf(currentCard);
  if (i > 0) goToCard(cards[i - 1]);
}

function handleVideoEnd(card) {
  markAsWatched(card.dataset.id);
  const toast = card.querySelector('.countdown-toast');
  if (toast) toast.classList.add('visible');
  const gen = ++navGen;
  clearTimeout(autoAdvanceTimeout);
  autoAdvanceTimeout = setTimeout(() => {
    if (toast) toast.classList.remove('visible');
    card.dataset.endedHandled = '';
    if (gen !== navGen || currentActiveCard !== card) return;   // user moved on
    advanceToNextReel(card);
  }, 350);
}
```
In `playCardVideo()` add `navGen++;` as the first statement so any user-initiated play cancels pending auto-advance timers. Delete the `feed.style.scrollSnapType` lines. Keep `checkCenteredCard` as the user-scroll settle path; it is now the only thing that plays after a *user* scroll, and `goToCard` is the only thing that plays after a *programmatic* one. If a crossfade is wanted, add `.reel-card { transition: opacity .12s } .reel-card.entering { opacity: 0 }` and toggle the class around the `scrollIntoView`; do not reintroduce smooth scrolling inside the snap container.

---

### P2 · S1 · No `error` / stall handling: one dead URL ends hands-free playback

**ELI15.** A `<video>` whose source 404s, or whose Range response stalls on cellular, never emits `timeupdate` or `ended`. The viewer only advances on those two events. It sits on a black card indefinitely.

**Remediation.** Progress watchdog + `error` listener; dead cards are skipped, hidden, and *not* marked watched:
```js
let lastProgressAt = performance.now();
let lastProgressTime = -1;

function skipDeadCard(card, why) {
  if (!card || card.dataset.dead) return;
  card.dataset.dead = '1';
  console.warn('Skipping dead reel', card.dataset.id, why);
  showToast('Reel unavailable, skipping');
  const cards = visibleCards();
  const i = cards.indexOf(card);
  card.style.display = 'none';
  const next = cards[i + 1] || cards[i - 1];
  if (next) goToCard(next); else filterCategory(currentCategory);
}

// inside the per-card wiring loop:
      video.addEventListener('timeupdate', () => {
        if (card === currentActiveCard && video.currentTime !== lastProgressTime) {
          lastProgressTime = video.currentTime; lastProgressAt = performance.now();
        }
        ...existing body...
      });
      video.addEventListener('error', () => { if (card === currentActiveCard) skipDeadCard(card, 'error'); });

// global, once:
setInterval(() => {
  const card = currentActiveCard; if (!card) return;
  const v = card.querySelector('.reel-video'); if (!v || v.paused || v.ended) return;
  if (v.error) return skipDeadCard(card, 'media error');
  if (performance.now() - lastProgressAt > 8000 && v.readyState < 3) skipDeadCard(card, 'no progress 8s');
}, 2000);
```
In `playCardVideo()` reset `lastProgressAt = performance.now(); lastProgressTime = -1;`.

---

### P3 · S2 · Every cold open downloads all 200 posters (~13 MB) and retains thumbnail blobs forever

**ELI15.** WebKit fetches a `<video poster>` as soon as it parses the tag, whether or not the card is on screen. The page has 200 cards, each with a 1200×630 JPEG poster served from GitHub Pages. Verified on `gh-pages`: 200 posters, all on `vkr1729.github.io`, ~80–120 KB each → ~13 MB on every launch before the first reel even plays. Separately, `cachedThumbnailFiles` keeps a `File` for every reel that ever entered the sliding window; over a 200-reel session that is another ~13 MB of heap the PWA never releases.

**Root cause.** `viewer.html:927` (`poster="{{ item.thumbnail }}"` on all cards), `:1142-1160` (unbounded Map).

**Remediation.**
Template: `poster` only for the first three cards; everyone else gets `data-poster`:
```html
          {% if loop.index0 < 3 %}poster="{{ item.thumbnail }}"{% endif %}
          data-poster="{{ item.thumbnail }}"
```
`updateSlidingWindow()`: manage posters with the same window (±3 is plenty):
```js
        const posterWindow = (distance >= -1 && distance <= 3);
        if (posterWindow) { if (!video.poster && video.dataset.poster) video.poster = video.dataset.poster; }
        else if (video.poster) { video.removeAttribute('poster'); }
```
Bound the share-thumbnail cache to six entries (LRU):
```js
const THUMB_CACHE_MAX = 6;
function rememberThumb(id, file) {
  cachedThumbnailFiles.delete(id); cachedThumbnailFiles.set(id, file);
  while (cachedThumbnailFiles.size > THUMB_CACHE_MAX) cachedThumbnailFiles.delete(cachedThumbnailFiles.keys().next().value);
}
```
Also shrink the share thumbnail itself: `-q:v 2` at 1200×630 is 80–120 KB; `-q:v 5` is ~45 KB and indistinguishable on a WhatsApp card.

---

### P4 · S2 · Web Share loses its user-activation token behind an `await fetch()` and then swallows the failure

**ELI15.** iOS only allows `navigator.share()` while the tap that triggered it is still "hot" (transient activation, roughly one second and cleared by the first `await` that yields to the network). The share handler awaits a thumbnail download *before* calling `share()` whenever the thumbnail isn't already cached, so `share()` throws `NotAllowedError` — and the code treats `NotAllowedError` the same as "user cancelled" and returns silently (`viewer.html:1210-1213`). To the user the Share button "does nothing" on any reel whose thumbnail wasn't prefetched (fast swiping, first reel after a jump, category switch).

**Remediation.** Never await before `share()`; share what is already in hand; only `AbortError` means "user cancelled":
```js
async function shareReelWhatsApp(reelId, creatorHandle, e) {
  if (e) { e.stopPropagation(); e.preventDefault(); }
  const shareUrl = `${basePath()}/share/${reelId}.html?v=3`;
  const shareText = `Watch @${creatorHandle || 'reel'} on Instagram Digest: ${shareUrl}`;
  const title = `Reel by @${creatorHandle || 'creator'}`;
  const file = cachedThumbnailFiles.get(reelId);   // synchronous lookup only
  if (navigator.share) {
    try {
      if (file && navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title, text: shareText });
      } else {
        await navigator.share({ title, text: shareText, url: shareUrl });
      }
      return;
    } catch (err) {
      if (err && err.name === 'AbortError') return;          // user dismissed the sheet
      console.warn('navigator.share failed, using deep link:', err);
    }
  }
  const isMobile = /iPhone|iPad|iPod|Android/i.test(navigator.userAgent);
  const deep = `whatsapp://send?text=${encodeURIComponent(shareText)}`;
  if (isMobile) window.location.href = deep;
  else window.open(`https://api.whatsapp.com/send?text=${encodeURIComponent(shareText)}`, '_blank');
}
```
`preloadReelThumbnail` stays as the background warmer (it already runs for the sliding window).

---

### P5 · S2 · Silent-mute fallback with no indicator; first reel is always muted until the first touch

**ELI15.** On load, `filterCategory('all')` calls `play()` on the first reel with no user gesture. iOS rejects unmuted autoplay, the catch mutes the element and plays muted, and nothing on screen says so. `restoreAudioOnInteraction` unmutes on the next touch, so the user experiences "the first reel has no sound until I tap".

**Remediation.** Show a tap-to-unmute pill whenever `video.muted && !isAudioMuted`, and clear it on `volumechange`:
```js
      playPromise.catch(() => {
        video.muted = true;
        video.play().catch(() => {});
        showMutePill(card);   // "🔇 Tap for sound" — a `.mute-pill` in the scrim; hide it in restoreAudioOnInteraction()
      });
```
Optional but recommended for iOS 26: on the very first user gesture, call `video.play()` *inside* the gesture handler for the active card (not via `setTimeout`) — the code already listens for `touchstart` once; make `handleInitialInteraction` call `activeVideo.muted = false; activeVideo.play()` directly.

---

### P6 · S2 · Watched-state drift across three storage islands, plus a legacy-key footgun

**ELI15.** Watched ids live in `localStorage`. On the phone there are two separate `localStorage`s for the same site: the Home Screen app and Safari (share links from WhatsApp open in Safari). Neither talks to the desktop's `watched.json`. So the same reel can be "watched" in one place and "unread" in two others. Also, every `markAsWatched` writes a global legacy key `ig_digest_watched_ids`, and `getWatchedIds()` *imports that key into any week whose key is empty* (`viewer.html:1000-1008`). Today the ids differ week to week so it's harmless, but the moment a reel repeats across weeks, last week's watched state leaks into this week, and the legacy key is never pruned.

iOS storage note: WebKit's 7-day script-storage cap does **not** apply to Home Screen web apps, so the PWA's `localStorage` is stable as long as the app icon exists; the Safari copy *is* subject to the cap.

**Remediation (bug-level; the cross-device fix is Feature 2 in the features document).**
```js
function getWatchedIds() {
  try { return new Set(JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')); }
  catch (e) { return new Set(); }
}
// remove every write to 'ig_digest_watched_ids'; on startup, prune stale week keys:
(function pruneOldWeeks() {
  const keep = new Set({{ available_weeks | map(attribute='week_id') | list | tojson }});
  Object.keys(localStorage).forEach(k => {
    const m = k.match(/^ig_digest_(?:watched_ids|last_watched_id)_(\d{4}-\d{2}-\d{2})$/);
    if ((m && !keep.has(m[1])) || k === 'ig_digest_watched_ids') localStorage.removeItem(k);
  });
})();
```

---

### P7 · S3 · Every double-tap first toggles play/pause

**ELI15.** The card's `click` handler toggles play/pause on *every* tap; the feed's `click` handler detects double-taps. So a double-tap to skip pauses on tap 1 and resumes on tap 2 with a ▶/❚❚ flash in between. Center double-tap toggles fullscreen *and* play/pause.

**Remediation.** Defer the single-tap action and cancel it when a second tap arrives:
```js
let singleTapTimer = null;
card.addEventListener('click', (e) => {
  if (e.target.closest('.creator-meta') || e.target.closest('.caption-snippet') || e.target.closest('button')) return;
  clearTimeout(singleTapTimer);
  singleTapTimer = setTimeout(() => { /* existing play/pause toggle body */ }, 330);
});
// in the feed double-tap branch, first line: clearTimeout(singleTapTimer);
```

---

### P8 · S3 · `preload="auto"` is a no-op on iOS; the "2-reel lookahead" only warms metadata

**ELI15.** iOS ignores `preload="auto"` and treats it as `metadata` (it will not buffer media data for a video that is not playing). The lookahead therefore only fetches the MP4 header, and the first frame on advance still waits for a fresh Range request (~300–800 ms on LTE). `fetchPriority` on `<video>` is not honoured by WebKit either.

**Remediation.** What *does* work on iOS is an explicit `load()` on the next element with `preload="metadata"` (fetches the `moov` atom and the first media chunk), plus keeping the Worker's `immutable` cache headers (verified present). In `updateSlidingWindow()`:
```js
          if (distance === 1 && !video.dataset.warmed) { video.preload = 'metadata'; video.load(); video.dataset.warmed = '1'; }
```
and clear `dataset.warmed` when `src` is removed. Don't expect more than this from iOS; the real lever is P3 (stop competing with 13 MB of posters for the same connection).

---

## 4. Major Performance Bottlenecks

| # | Bottleneck | Where | Impact | Fix |
| --- | --- | --- | --- | --- |
| B1 | **Serial download phase.** 200 × (metadata page load ≈ 3 s + download ≈ 5–15 s) ≈ 30–60 min, single-threaded, browser held open. | `main.py:127-165` | Long Friday runs; CDN URLs captured early can expire before use. | After C3/C4, resolve CDN URLs in the enrichment pass, close Playwright, then download with `ThreadPoolExecutor(max_workers=4)` over `requests` in creator-interleaved order (avoid hammering one CDN shard). Upload from the same worker on success. |
| B2 | **Per-object R2 round-trips.** `head_object` per upload (200) + `delete_object` per purge key. | `storage_r2.py:188, 124` | ~400 extra API calls/run; slow on a flaky link. | `list_objects_v2(Prefix=f"videos/{week_id}/")` once → set; batch deletes with `delete_objects` (up to 1 000 keys). |
| B3 | **Poster storm.** 200 eager posters ≈ 13 MB/launch. | `viewer.html:927` | 5–15 s of contention on LTE before the first reel; battery. | P3. |
| B4 | **Thumbnail generation serial with 5 s timeouts.** 200 × ffmpeg, worst case 17 min. | `site_builder.py:103-116` | Slow builds; `--build-only` reruns pay it again if any thumb failed. | Run in a `ThreadPoolExecutor(4)`; lower `timeout` to 3; use `-q:v 5`. |
| B5 | **Full-DOM sweeps on hot paths.** `playCardVideo` iterates all 200 `<video>` and sets `currentTime = 0`; `updateCategoryProgressRings` recomputes 7 × 200 on every watched mark. | `viewer.html:1617-1622, 1448-1481` | Minor jank on advance (200 attribute writes on elements with no src is cheap but not free). | Only reset videos inside the sliding window (they are the only ones with `src`); throttle ring updates with `requestAnimationFrame`. |
| B6 | **567 KB HTML with 200 inline cards.** | `site_builder.py` render | Parse + style ≈ 150–250 ms on iPhone 16; grows with `TOP_DIGEST_COUNT`. | Optional: render cards from `data.json` client-side with a 12-card virtual window. Only worth it if the digest grows past ~300 or the poster fix isn't enough. |

---

## 5. Execution Blueprint (ordered for an autonomous coding agent)

Constraints for the agent: flat two-layer "boring code", no new frameworks, no new runtime dependencies, keep every existing CLI flag and file path, run `pytest tests/` after each step, and re-render `site/local_index.html` with `python main.py --build-only` to eyeball the viewer.

- [ ] **Step 1 — Viability gate & block detection (C1, C5).** `extractor.py`: add `InstagramBlocked`, `_assert_not_blocked`, re-raise in `discover_creator_reel_urls`. `main.py`: expected-count accounting, `MIN_CANDIDATE_RATIO`, `MAX_EMPTY_CREATOR_RATIO`, jittered sleep, empty-streak backoff, delete cache on abort, return code 2, `MIN_DEPLOY_ITEMS` deploy gate, delete the `candidates[:N]` fallback. `run_weekly.sh`: exit-code-2 message. Test: simulate `page.url` = `/accounts/login` → run exits 2, no `top100_digest.json` mutation, no push.
- [ ] **Step 2 — Playable-only digest (C2).** `storage_r2.upload_reel_to_r2` returns `""` on remote failure; `main.py` drops unplayable ids and moves `save_digest_batch` after uploads; `site_builder.build_site` skips ids missing from a provided URL map. Test: mock one failed upload → item absent from `index.html`, `data.json`, and `share/`.
- [ ] **Step 3 — Honest metrics & two-pass ranking (C3).** Fast-mode fields zeroed + `metrics_estimated`; `compute_viral_score` skips engagement when estimated; two-pass select/enrich/re-rank; cutoff filter on real timestamps; remove enrichment from download loop. Test: `tests/test_ranker.py` case where two reels differ only in `metrics_estimated` → identical score; `data.json` after a `--dry-run` with a stub session has real captions.
- [ ] **Step 4 — Playwright hygiene (C4).** Context recycle every 40 navigations; session closed before downloads. Test: unit test that `get_page()` returns a new page object after 40 calls.
- [ ] **Step 5 — Viewer navigation engine (P1, P2, P7).** Replace timers with `navGen` + `goToCard`; add watchdog and `error` handling; deferred single-tap. Test: `tests/e2e/test_ui_interactions.py` — (a) auto-advance moves exactly one card; (b) a card whose `data-src` 404s is skipped within 10 s and *not* in `localStorage` watched set; (c) double-tap does not emit a pause.
- [ ] **Step 6 — Poster & memory diet (P3, P8).** `data-poster`, windowed poster assignment, LRU thumb cache, `load()` warm-up. Test: after initial render, `document.querySelectorAll('video[poster]').length <= 5`.
- [ ] **Step 7 — Share activation fix (P4).** No awaits before `navigator.share`; only `AbortError` is silent. Test: stub `navigator.share` to throw `NotAllowedError` → deep link fallback invoked.
- [ ] **Step 8 — Mute indicator (P5).** Mute pill + first-gesture unmute inside the handler.
- [ ] **Step 9 — Storage hygiene (P6).** Remove legacy key; prune stale week keys.
- [ ] **Step 10 — Site pruning & expiry (C6, C8).** `_prune_site_assets`, share-page `onerror` message, `RETENTION_DAYS = weeks*7+1`, week selector only lists weeks that still exist on R2.
- [ ] **Step 11 — Local server safety (C7).** Lock + atomic writes + proper `do_HEAD`.
- [ ] **Step 12 — Throughput (B1, B2, B4).** Threaded downloads/uploads, single R2 listing per week, batched deletes, threaded ffmpeg.

**Definition of done for the whole plan:** a run with (a) Instagram blocked, (b) 5 % of downloads failing, and (c) one R2 upload failing, produces either no deploy (a) or a deploy where every card plays and auto-advance runs 20 cards hands-free on an iPhone without a single manual swipe (b, c).
