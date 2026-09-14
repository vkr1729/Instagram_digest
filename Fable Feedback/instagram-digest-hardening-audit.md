# Instagram Digest: post-hardening adversarial audit

| | |
|---|---|
| **Repository** | https://github.com/vkr1729/Instagram_digest |
| **Audited revision** | `6bcdb92` (HEAD, 2026-09-12), six commits after the hardening commit `805bae4` |
| **Claim under test** | `CHANGELOG.md` / `ARCHITECTURE.md`: seven P0 findings closed, score F/39 → B+/87 |
| **Audit date** | 2026-09-12 |
| **Method** | Source review of every claimed fix against the code at HEAD; full non-browser test run; 13 executable adversarial probes (attached as `test_audit_probes.py`, each PASS reproduces a defect) |

## Summary

Three of the eight hardening claims hold, four are weaker than documented, and one is broken. The pass did real work: the PWA XSS sinks, the service-worker range logic, the fsync-backed atomic writer and the id-keyed pruner are correct and were verified by execution. However two P0-class defects remain in the most routine operator flow, and the hardening's own regression test for one of them seeds a data shape the pipeline never produces, so the suite is green while the invariant it names is false.

**Revised score: C+ / 68** (from the claimed B+ / 87).

| # | Claim | Verdict | One-line reason |
|---|---|---|---|
| 1 | Stored XSS closed | **WEAK** | PWA sinks fixed; the local ops page `channels.html` still injects Instagram display names into `innerHTML` unescaped |
| 2 | Durability / locking / quarantine | **WEAK** | Atomic writer is correct; the pipeline lock is per-process only, several state writes bypass it, one corrupt-file path deletes instead of quarantining |
| 3 | Pruner safety | **HOLDS** | Id-suffix matching can spare wrongly but never delete wrongly; torn digests skipped |
| 4 | Expand coherence | **BROKEN** | Any +100 on a later UTC day than the sync re-points every existing reel at a non-existent key; duplicate ids on resume; rank collisions |
| 5 | SW RFC 7233 ranges | **HOLDS** | Passes the shipped matrix plus extra edge cases; two pedantic RFC nits AVFoundation never triggers |
| 6 | Scraper anti-bot posture | **WEAK** | Fingerprint self-contradictions; per-account timezone rotation; no pool self-heal; docs drift |
| 7 | Parser resilience | **WEAK** | Soft-block detector scans the whole page including captions; false positives silently drop reels |
| 8 | DOM / memory scaling | **HOLDS** | `src` lifecycle is closed; containment scoped correctly; tests are marker greps only |

Test run at HEAD: **182 passed**; 5 failed and 13 errored, all environmental (Playwright not launchable, ffmpeg absent, and `tests/test_uat_suite7_server_launcher.py:14` asserts a `.desktop` file in the maintainer's home directory). All six hardening test files pass. All 13 audit probes reproduce their defect.

## 1. Verdicts with evidence

### Claim 1: XSS. WEAK

What holds:

- `templates/partials/feed.html:30,34`: share/unselect buttons pass values through `data-id` / `data-handle` and read `this.dataset`.
- `site_builder.py:274-280`: share pages escape handle, rank, caption, thumb, poster and reel id with `html.escape(..., quote=True)`.
- `templates/partials/player.js:74`: `currentWeekId` rendered with `|tojson`. The only other Jinja expressions in the JS partial are `player.js:11` (a float) and `player.js:147` (`|tojson`).
- Note for future changes: `site_builder.py:72` uses `select_autoescape(["html", "xml"])`, so autoescape is **off** inside `player.js` and `styles.css`. Any new `{{ }}` in those partials renders raw.

What does not hold: stored XSS on the local ops origin.

- Data path A: Instagram `full_name` → `extractor.py:270` (`name = u.get("full_name")`) → `save_sources` at `extractor.py:335` → `GET /api/channels` returns it raw at `local_server.py:943` → `templates/channels.html:553-576` builds `innerHTML` with `${c.name}`, `${c.handle}`, `${c.category}` and `onclick="singleAction('${c.handle}', ...)"` with no escaping (`grep -c "esc(" channels.html` = 0).
- Data path B: for external reels the "handle" is derived from link `innerText` (`extractor.py:1087-1096`, any token without spaces). Pressing B in the viewer posts that string to `/api/blacklist` (`local_server.py:1117`), and blacklisted handles are rendered by the same sink via `local_server.py:948-955`.
- Impact: the payload runs same-origin on `127.0.0.1:8080`, so `_is_local_origin` (`local_server.py:396-413`) passes. It can POST `/api/server/shutdown` (1216), `/api/expand` (992) and `/api/channels/bulk-unselect` (1155), which rewrites `sources.json`.
- Probe J reproduces the raw round-trip and the unescaped sink.

### Claim 2: Durability, locking, quarantine. WEAK

What holds:

- `atomic_io.py:19-47`: `mkstemp` in the target directory → write → `flush` → `fsync` → `os.replace` → directory `fsync`, temp cleanup on failure only. Correct.
- `ranker.py:227,233`, `local_server.py:558-581`: adopted as claimed; `_load_json_tolerant` copies corrupt files to `*.corrupt-<ts>` at the 11 call sites in `local_server.py`.

What does not hold:

- **The pipeline lock is per-process.** `_PIPELINE_LOCK` at `local_server.py:34` is a `threading.Lock`. `run_weekly.sh:30` (Friday cron) and `resume_pending.sh:115,129` (login resume) run `main.py` as separate processes, while dashboard-triggered runs execute in server threads (`local_server.py:109,196`). There is no `flock`/`fcntl` anywhere in the Python sources. Probe F: two processes both acquire the lock. `ARCHITECTURE.md:139` ("Sync and expand collide → second caller gets already_running") is false for the deployed topology. Collision effects: last `os.replace` of the digest wins; both `deploy_to_gh_pages` calls `rmtree(site/.git)` and `git init` in the same directory (`site_builder.py:401-432`); `run_expand` ignores the deploy return value (`main.py:1148`).
- **State writes that bypass `durable_write_json`**: `extractor.py:120` (`save_sources`, plain `write_text`, called from `POST /api/sync-following` at `local_server.py:1027` outside `_STATE_LOCK` and racing the atomic `sources.json` writes at 1141/1196); `extractor.py:342` (following cache); `site_builder.py:246,258,337` (`index.html`, `local_index.html`, `data.json`). `ARCHITECTURE.md:42` calls `index.html` atomic; it is not. Probe G.
- **Reads that still treat corrupt as empty, or worse**: `main.py:863-885` reads an unparseable expand checkpoint as `[]` and then **unlinks it** at line 883 (probe E), the opposite of quarantine; `main.py:34-41` returns `None` for a corrupt `last_run.json`, silently losing the ad-hoc anchor at `main.py:1210-1215`; `extractor.py:92-100` and `ranker.py:69-77` return an empty blacklist, so muted creators reappear.

### Claim 3: Pruner safety. HOLDS

- `storage_r2.py:193-195`: `_is_referenced` matches `key.endswith(f"_{rid}.mp4")`. Because every live key ends with its own `_{id}.mp4` by construction (`main.py:666`, `main.py:1054`), suffix matching can only spare an unrelated object, never delete a live one. Handle underscores sit before the id and cannot affect the match.
- `storage_r2.py:173-181`: unparseable digests are skipped and empty item lists are never ground truth. Verified by the shipped test and by inspection.
- Week keying: ids are grouped by `run_date` (line 179). After the run_date drift described under claim 4, the archive `digests/<sync-day>.json` still anchors the sync week's ids, and nothing in the codebase deletes files under `DIGESTS_DIR`, so no wrong deletion follows.
- Nit: line 219 `purged.extend([...Deleted...] or chunk)` reports keys as purged when every delete in the chunk failed (probe M). Errors are logged and orphans are retried on the next run, so this is a reporting defect only.

### Claim 4: Expand coherence. BROKEN

The two-phase flow inside one run (`main.py:1017-1110`) is correct, and that is exactly what the shipped test proves. The invariant fails across runs.

- **Week drift (P0).** `run_full_sync` never persists the uploaded URL on the reel: uploads only populate `uploaded_url_map` (`main.py:662-698`), `save_digest_batch` runs at line 736, and the `r2_url` mutation happens later inside `build_site` (`site_builder.py:100-102`) on dict objects whose digest is already on disk. Probe A confirms the saved digest has no `r2_url`/`video_url`. `run_expand` sets `week_id = today` at line 791 and falls back to `videos/{week_id}/…` for existing items at line 1014; `site_builder.py:89-90` derives `local_url` the same way. Therefore any +100 on a later UTC day than the sync (Friday 22:00 cron followed by a weekend click, or the `resume_pending.sh` chain at next login) re-points **every** existing reel at a key that does not exist, on both R2 and the local server. Probe B: 3 of 3 existing URLs wrong. `run_expand` has no `MIN_DEPLOY_ITEMS` gate (line 1147), so `--deploy` force-pushes the dead site. The regression test masks this by seeding existing items with `r2_url` (`tests/test_durability.py:116,120`, `tests/test_expand_resume.py:44`), a shape the sync never produces.
- **Duplicate ids on resume (P1).** `main.py:903-904` unions checkpoint ids into `existing_ids` but never filters `resumed` against the digest (line 919). `resume_pending.sh:113-131` runs sync then expand in that order; if the sync's own discovery pass integrated a banked reel, the resumed expand appends it again. Probe C: manifest ids `["X", "X"]`.
- **Rank collisions (P2).** The sync drops unplayables without renumbering (`main.py:719-722`); expand assigns `len(existing_items) + 1` (line 1051). Probe D: ranks `[1, 2, 4, 4]`.
- The `_pending_` reuse branch at line 1022 is unreachable: lines 1003-1007 delete every pending file first.

### Claim 5: SW ranges. HOLDS

- `templates/sw.js:40-90` passes the shipped 12-case Node matrix. Additional cases run through the same harness: `bytes=-5000` on a 1000-byte blob → `206 bytes 0-999/1000`; `bytes=-1` → `206 bytes 999-999/1000`; `bytes=0-1` → `206`; `bytes=999-` → `206`; empty blob → `416 bytes */0`; `bytes=-` → `416`.
- Two pedantic gaps that AVFoundation does not exercise: the range unit is case-insensitive per RFC 9110 §14.1 (`Bytes=0-99` gets 416), and a syntactically invalid `Range` should be ignored with a 200 rather than answered 416 (`sw.js:53-55`).
- Full-blob materialization per request at `sw.js:110` is the documented known limit.

### Claim 6: Scraper. WEAK

- Pools and pacing exist as described (`extractor.py:38-51,71-81`). The enrichment session queue's `get` / `finally: put` (`main.py:520-529`) is exception-safe.
- `ENRICH_WORKERS = 1` at `main.py:86`; `CHANGELOG.md:110` and `ARCHITECTURE.md:61` say 2.
- Fingerprint self-contradictions (probe L): `LOCALE_POOL` includes `en-GB` (line 50) while the stealth script hardcodes `navigator.languages = ['en-US', 'en']` (line 60); the `webdriver` getter returns `undefined` (line 57) where real Chrome returns `false`; `plugins` is a plain Array (line 59), not a `PluginArray`; the UA pool pins Chrome 130/131 (lines 38-47) while Playwright's bundled Chromium reports its real version in `Sec-CH-UA`, which a UA override does not rewrite.
- Rotating timezone across New York / London / Kolkata per context (lines 484-491, every 40 navigations via `RECYCLE_EVERY`) on a single authenticated account is an account-linking anomaly rather than camouflage.
- No pool self-heal: a crashed browser is returned to the queue (`main.py:529`), `get_page()` (`extractor.py:547-560`) keeps handing out the dead page, every enrichment fails at debug level (`extractor.py:784-785`), and the run aborts with "no qualifying reels".
- Following-API backoff (`extractor.py:243-260`) gives up after 2/4/8 seconds; Instagram 429 windows last minutes. The merge is non-destructive, so the damage is a partial following list.

### Claim 7: Parsers. WEAK

- `_page_html_indicates_block` (`extractor.py:407-408`) lowercases the **entire** page, including captions, comments and inline JavaScript, and is applied per reel page at lines 693-696 with no fallback. Probe K: a caption containing "try again later" makes a fully valid reel return `None`. `login_required` and `challenge_required` are genuine Instagram API error codes; if either appears as a string constant in the inline bundle, every enrichment returns `None` and the run aborts. The test corpus (`tests/test_parser_resilience.py:31-36`) contains no false-positive case.
- `_DISCOVERY_SELECTORS` (line 440): the second selector `a[href*='/reel']` is a superset of the first, so the "fallback chain" cannot match anything the first did not.
- `_extract_shortcode` (443-447) and `_parse_date_flexible` (417-437) behave as documented; naive dates pinned to UTC carry up to ±14 h of error at the window boundary.

### Claim 8: DOM performance. HOLDS

- `templates/partials/styles.css:508-512` (containment) and `527-530` (single `will-change`, scoped to `.is-active`).
- `src` lifecycle is closed: set at `player.js:938`, `1109`, `1186`; removed at `969`, `1021-1026`, `1127-1133`. The only play path that bypasses `updateSlidingWindow` is the Space key (line 1639), which acts on an already-loaded active video.
- Cards have explicit heights (`styles.css:492-506`), so `content-visibility: auto` does not disturb snap geometry, and `scrollIntoView` (line 895) forces rendering per spec.
- Cosmetic: the "#N" display uses `data-index` (lines 921-925, 1171-1175), so after `unselectCreator` removes cards (1572-1576) it shows a DOM index, not a rank; `handleJumpSubmit` indexes all cards including dead ones (1476, 1499), so a jump can land on a hidden card and show nothing.
- `tests/test_dom_perf.py` is four static string greps; none of the above was verified by a test.

## 2. Test-suite audit

| File | Behavior or markers? | Highest-risk behavior it does not cover |
|---|---|---|
| `test_xss_hardening.py` | Behavior for the PWA (renders the real template), one payload | The ops-origin sink in `channels.html`; the notifier; the autoescape-off JS partial |
| `test_durability.py` | Mixed. Pruner tests are real. `test_r2_keys_fast_path_is_thread_safe` (185-200) locks its own closure, not `upload_reel_to_r2`. The atomic-write test has no fault injection. The expand test seeds `r2_url` the sync never writes | Sync on day N, expand on day N+1; cross-process collision; corrupt-checkpoint handling |
| `test_sw_range.py` | Behavior: executes the shipped JS in Node (the strongest file). `test_sw_source_has_no_clamping_fallback` is a marker | The fetch handler's cache-miss + Range passthrough |
| `test_scraper_hardening.py` | `human_pause` distribution is real; the context test asserts `"webdriver" in script`; API backoff is real | `extract_external_reels_from_feed` has no test at all (partial salvage, cooldown schedule, `on_progress`); pool self-heal |
| `test_parser_resilience.py` | Behavior with fakes | Any false-positive case for the soft-block detector; locale date variants |
| `test_dom_perf.py` | Markers only (all four tests) | `src` detachment outside the window; jump onto a dead card |

## 3. New findings, ranked

| Sev | Finding | Evidence | Probe |
|---|---|---|---|
| P0 | Expand on a later UTC day re-points every existing reel to `videos/<today>/`; `--deploy` publishes the dead site | `main.py:791,1014`; `site_builder.py:89-90`; `main.py:736` vs `site_builder.py:100-102` | A, B |
| P0 | No cross-process mutual exclusion between cron / resume CLI runs and dashboard threads | `local_server.py:34`; `run_weekly.sh:30`; `resume_pending.sh:115`; `site_builder.py:401-432` | F |
| P1 | Stored XSS on the ops origin via Instagram display name or external-reel handle | `channels.html:553-576`; `extractor.py:270`; `local_server.py:943` | J |
| P1 | Checkpoint resume re-appends ids an intervening sync already integrated | `main.py:903-919`; `resume_pending.sh:113-131` | C |
| P1 | `GET /retrigger` starts sync + deploy with no origin check; an `<img src>` on any web page fires it | `local_server.py:648-649` vs the POST guard at 974 | H |
| P1 | Soft-block detector false-positives on captions or inline JS; drops reels or aborts runs | `extractor.py:394-408,693-696` | K |
| P2 | Corrupt expand checkpoint is deleted, not quarantined | `main.py:881-885` | E |
| P2 | Rank collisions after sync-time drops | `main.py:719-722,1051` | D |
| P2 | Dot-segment traversal on `/videos/` and static routes (raw clients only; browsers normalize). Separately, the no-Range branch of `serve_video_file` never calls `end_headers()`, so a plain GET returns the body with no status line. Pre-existing since 2026-09-06. The SW's `precacheUrls` fetch (`sw.js:164`) against the local server can therefore never succeed | `local_server.py:754-763,1239-1249` | I |
| P2 | `sources.json` written non-atomically and outside `_STATE_LOCK` | `extractor.py:120`; `local_server.py:1027` vs 1141 | G |
| P2 | Pruner reports failed deletes as purged | `storage_r2.py:219` | M |
| P2 | Fingerprint contradictions and per-account timezone rotation | `extractor.py:50,57,59,60,484-491` | L |

Failure mechanisms for the two P0s:

1. **Week drift.** Friday 22:00 cron runs `main.py --sync --deploy`; `week_id = 2026-09-11`; objects land in `videos/2026-09-11/`; the digest is saved without `r2_url`. Saturday the owner clicks +100 (or `resume_pending.sh` fires at login). `run_expand` sets `week_id = 2026-09-12`, builds `uploaded_url_map` for the 300 existing items from the fallback `videos/2026-09-12/<rank>_<handle>_<id>.mp4`, uploads the new 100 under the same prefix, saves the digest with `run_date = 2026-09-12`, rebuilds the site with 300 dead cards and 100 live ones, and force-pushes it. In the PWA each dead card fires a media error and `skipDeadCard` shows "Reel unavailable, skipping" 300 times.
2. **Cross-process collision.** The Friday cron sync is mid-download. The owner opens the dashboard and clicks +100; the server process's `_PIPELINE_LOCK` is free, so `run_expand` starts against last week's digest. Both processes write `top100_digest.json` via `os.replace` (last writer wins), both run `purge_unreferenced_r2_videos` against whichever digest is on disk at that moment, and both run `deploy_to_gh_pages`, which deletes and re-creates `site/.git` under the other's feet. `run_expand` returns 0 regardless of the deploy result.

## 4. Score revision

B+/87 assumed all seven P0s closed. The verified improvements are real: PWA XSS sinks, service-worker ranges, the atomic writer and the id-based pruner are correct, and the first three are proven by executing code rather than reading it. Against that, two P0-class defects sit in the most routine operator flow, one of them hidden by a regression test that encodes a digest shape the sync never writes. Half of the new tests are marker greps, `CHANGELOG.md` and `ARCHITECTURE.md` assert properties the code does not have (atomic `index.html`, cross-pipeline exclusion, 2-browser enrichment), and the ops surface was left out of the XSS scope. **Revised: C+ / 68.**

## 5. Minimal diffs for the top three issues

### Diff 1: week drift (P0)

Persist the real object URL at sync time, and make an expansion belong to the digest's week rather than the calendar day it runs on.

```diff
--- a/main.py
+++ b/main.py
@@ def run_full_sync(
         dropped = [r["id"] for r in ranked_reels if r["id"] not in uploaded_url_map]
         if dropped:
             logger.warning("Dropping %d unplayable reels: %s", len(dropped), ", ".join(dropped))
             ranked_reels = [r for r in ranked_reels if r["id"] in uploaded_url_map]
+        # Persist the real object URL so later expansions never derive keys from today's date.
+        for r in ranked_reels:
+            r["r2_url"] = r["video_url"] = uploaded_url_map[r["id"]]
 
@@ def run_expand(target_count: int = 100, deploy: bool = False) -> int:
-    week_id = datetime.now(timezone.utc).strftime("%Y-%m-%d")
-    logger.info("Starting +%d reel expansion for week %s (deploy=%s)...", target_count, week_id, deploy)
-
     if not config.DIGEST_BATCH_FILE.exists():
         logger.error("No active digest found (%s). Run full sync first.", config.DIGEST_BATCH_FILE)
         return 1
@@
     existing_items: list[dict[str, Any]] = digest_data.get("items", [])
     if not existing_items:
         logger.error("Active digest has 0 items. Run full sync first.")
         return 1
+    # An expansion belongs to the digest's week, not the calendar day it runs on:
+    # R2 keys, local files, checkpoints and the pruner are all keyed on run_date.
+    week_id = digest_data.get("run_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
+    logger.info("Starting +%d reel expansion for week %s (deploy=%s)...", target_count, week_id, deploy)
```

### Diff 2: cross-process pipeline lock (P0)

```diff
--- a/main.py
+++ b/main.py
@@
 import argparse
+import fcntl
 import json
 import logging
 import math
+import os
 import random
 import sys
 import time
+from contextlib import contextmanager
@@
+@contextmanager
+def _pipeline_file_lock():
+    """Cross-process exclusion for digest-mutating pipelines. local_server's
+    threading.Lock cannot see cron / resume_pending.sh / manual CLI runs."""
+    fd = os.open(config.DATA_DIR / ".pipeline.lock", os.O_RDWR | os.O_CREAT, 0o600)
+    try:
+        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
+    except BlockingIOError:
+        os.close(fd)
+        raise RuntimeError("another pipeline (sync/expand) holds data/.pipeline.lock")
+    try:
+        yield
+    finally:
+        fcntl.flock(fd, fcntl.LOCK_UN)
+        os.close(fd)
+
+
 def run_full_sync(
     dry_run: bool = False,
     deploy: bool = False,
     days_back: int = 7,
     limit_per_creator: int = 15,
     since_timestamp: int | None = None,
 ) -> int:
+    try:
+        with _pipeline_file_lock():
+            return _run_full_sync(dry_run, deploy, days_back, limit_per_creator, since_timestamp)
+    except RuntimeError as exc:
+        logger.error("%s; refusing to start.", exc)
+        return 3
+
+
+def _run_full_sync(dry_run, deploy, days_back, limit_per_creator, since_timestamp) -> int:
     """Execute complete end-to-end extraction, ranking, upload, and deployment pipeline."""
@@
 def run_expand(target_count: int = 100, deploy: bool = False) -> int:
+    try:
+        with _pipeline_file_lock():
+            return _run_expand(target_count, deploy)
+    except RuntimeError as exc:
+        logger.error("%s; refusing to start.", exc)
+        return 3
+
+
+def _run_expand(target_count: int, deploy: bool) -> int:
     """
     Expand active digest by discovering N extra reels from the Reels feed.
```

`run_weekly.sh` treats exit 3 as a failure and emails the alert, which is the correct outcome for a refused start.

### Diff 3: ops-origin stored XSS (P1), plus the GET side effect it would ride

```diff
--- a/templates/channels.html
+++ b/templates/channels.html
@@
     let currentCategoryFilter = 'all';
 
+    function esc(s) {
+      return String(s == null ? '' : s).replace(/[&<>"']/g, c => (
+        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
+    }
+
@@
       listEl.innerHTML = filtered.map(c => {
+        const h = esc(c.handle);
+        const name = esc(c.name || '@' + c.handle);
+        const cat = esc(c.category || 'entertainment');
         const isChecked = selectedHandles.has(c.handle);
-        const initial = (c.handle[0] || '?').toUpperCase();
+        const initial = esc((c.handle[0] || '?').toUpperCase());
         return `
-          <div class="channel-row ${c.is_blacklisted ? 'is-blacklisted' : ''}" data-handle="${c.handle}">
-            <input type="checkbox" class="row-checkbox" value="${c.handle}" ${isChecked ? 'checked' : ''} onchange="toggleRowSelect('${c.handle}', this.checked)">
+          <div class="channel-row ${c.is_blacklisted ? 'is-blacklisted' : ''}" data-handle="${h}">
+            <input type="checkbox" class="row-checkbox" value="${h}" ${isChecked ? 'checked' : ''} onchange="toggleRowSelect(this.value, this.checked)">
             <div class="avatar-initial">${initial}</div>
             <div class="channel-info">
               <div class="channel-handle-row">
-                <a href="https://instagram.com/${c.handle}" target="_blank" rel="noopener" class="channel-handle">@${c.handle}</a>
-                <span class="cat-pill ${c.category || 'entertainment'}">${c.category || 'entertainment'}</span>
+                <a href="https://instagram.com/${encodeURIComponent(c.handle)}" target="_blank" rel="noopener" class="channel-handle">@${h}</a>
+                <span class="cat-pill ${cat}">${cat}</span>
               </div>
-              <div class="channel-name">${c.name || '@' + c.handle}</div>
+              <div class="channel-name">${name}</div>
             </div>
@@
             ${c.is_blacklisted
-              ? `<button class="btn-row-action restore" onclick="singleAction('${c.handle}', 'remove')">↺ Restore</button>`
-              : `<button class="btn-row-action danger" onclick="singleAction('${c.handle}', 'add')">🚫 Mute</button>`
+              ? `<button class="btn-row-action restore" onclick="singleAction(this.closest('.channel-row').dataset.handle, 'remove')">↺ Restore</button>`
+              : `<button class="btn-row-action danger" onclick="singleAction(this.closest('.channel-row').dataset.handle, 'add')">🚫 Mute</button>`
             }
--- a/local_server.py
+++ b/local_server.py
@@ def do_GET(self):
         if clean_path in ("/retrigger", "/retrigger/"):
-            trigger_adhoc_sync_task(deploy=True)
+            # GET only renders the page; the page POSTs, which carries a same-origin
+            # Origin header and passes _is_local_origin. Referer checks on GET are
+            # bypassable with Referrer-Policy: no-referrer, so do not rely on them.
             html_content = """<!DOCTYPE html>
@@
   <script>
+    fetch('/api/sync-adhoc', { method: 'POST' }).catch(() => {});
     async function checkStatus() {
```

Two one-line follow-ups outside the top three: add `self.end_headers()` after the last `send_header` in the no-Range branch of `serve_video_file` (`local_server.py:1243`), and add `resumed = [r for r in resumed if r["id"] not in existing_ids]` before `main.py:903`.

## Appendix: reproduction

Environment used for this audit: Python 3.13; `jinja2` 3.1.4 and `pytest` 8.3.4 vendored from GitHub tags (PyPI was unreachable on the audit network); `boto3`, `botocore`, `playwright` and `requests` replaced by minimal stubs, which is safe because every test that touches them already mocks them. Node 24 for the service-worker harness.

To rerun the probes in the maintainer's environment:

```bash
cp test_audit_probes.py tests/
.venv/bin/python -m pytest tests/test_audit_probes.py -v
```

Every probe that passes reproduces the defect named in its function name. Probe F spawns a subprocess with the same interpreter; probes H, I and J start a throwaway `LocalDigestHandler` on an ephemeral port. Nothing writes outside pytest's `tmp_path`.

Suite result at `6bcdb92` in the audit environment:

| Result | Count | Cause |
|---|---|---|
| passed | 182 | |
| failed | 5 | Playwright not launchable (3), ffmpeg absent (1), host-specific `.desktop` file assertion (1) |
| error | 13 | Playwright fixture in `tests/test_mobile_pwa_uat.py` |
