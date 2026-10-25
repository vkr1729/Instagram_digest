# UAT Plan — §6 Final Refactor, Stage 4e (Branch `arch/direct-meta-stream`)

- **Target user:** single user (Kedarnath Reddy), personal consumption on iPhone (iOS 17+)
- **Artifact under test:** `InstagramDigestDirect.ipa` built by `.github/workflows/build-ipa.yml`
  from the pushed `arch/direct-meta-stream` tip, plus the workstation pipeline (`main.py`,
  `site/data_direct.json`)
- **Requirements trace:** `.workflow/REQUIREMENTS.md` §1–§6; architecture per
  `.workflow/IMPLEMENTATION_PLAN.md` Phase A–F; audit per
  `.workflow/AUDIT_AND_REMEDIATION_REPORT.md` (Stage 4d: conditional pass, 98/100)

---

## 1. Core Automated Test Commands

Run in this order. All must be green before the manual iPhone pass begins.

### 1.1 Local Python suites (workstation, no network writes)

```bash
# Direct-stream pipeline contract + migration regression (31 tests)
.venv/bin/python -m pytest tests/test_direct_stream_mode.py tests/test_migrate_r2_bookmarks_to_gh.py -q
```

Expected: `31 passed`. Covers `DIRECT_STREAM_MODE` manifest shape, `video_cdn_url`
freshness fields, `size_bytes`, and AUDIT-014 migration cells (asset fields
present/absent × timestamp present/empty/null/missing).

### 1.2 Swift syntax check (workstation, Linux-safe)

```bash
export PATH="$HOME/.local/bin:$PATH"
swiftc -parse $(find Sources Tests -name '*.swift')
```

Expected: exit 0, zero diagnostics. Note: `-parse` validates syntax only; full
type-check + strict-concurrency + warnings-as-errors enforcement happens in §1.4.

### 1.3 Workstation pipeline dry checks (scoped — never bare `main.py`)

Per `AGENTS.md`, bare `python main.py` / `--sync` triggers a full production sync.
Use only these scoped forms:

```bash
# Dry simulation of discovery & ranking
.venv/bin/python main.py --dry-run
# Fast scoped test (few creators / short window)
.venv/bin/python main.py --dry-run --limit-per-creator 1 --days-back 1
# Static site compile without network or deploy
.venv/bin/python main.py --build-only
# Safe feed-expansion check
.venv/bin/python main.py --expand 3 --dry-run
```

Expected: each exits 0; `--build-only` regenerates `site/data_direct.json` with
fresh `video_cdn_url` entries while production `data.json` is byte-identical
(`git diff --stat` shows no `data.json` change). After any interrupted run,
clear transient checkpoints:

```bash
rm -f data/sync_progress_*.json data/expand_checkpoint_*.json data/expand_progress_*.json
```

### 1.4 GitHub Actions CI (macOS — the binding gate)

Trigger: push the §6 tree to `arch/direct-meta-stream` (Stage 5); workflow
`.github/workflows/build-ipa.yml` runs automatically. Alternatively,
`gh workflow run build-ipa.yml --ref arch/direct-meta-stream`, then:

```bash
gh run watch --exit-status
```

Expected: green run at the pushed tip SHA covering (a) `xcodebuild test` for
`InstagramDigestTests` (incl. all `testAudit*` cases) and `InstagramDigestUITests`
with `SWIFT_TREAT_WARNINGS_AS_ERRORS=YES` + `GCC_TREAT_WARNINGS_AS_ERRORS=YES`
(any warning fails the build); (b) packaged `build_artifacts/InstagramDigestDirect.ipa`
attached to the run. Download it for §3:

```bash
gh run download --name InstagramDigestDirect-ipa --dir build_artifacts
```

CI green at the tip SHA is a **ship-blocker** (§4): no freeze without it.

---

## 2. Requirements Traceability Matrix (§1–§6)

| # | Requirement (§) | What is verified | Automated | Manual (§3) |
|---|---|---|---|---|
| 1 | §1 Zero-cloud, single-user | No R2/Telegram code paths active in direct mode; infra files preserved untouched | §1.1, §1.3 | — |
| 2 | §1 Isolated benchmark channel | `data_direct.json` deployed; production `data.json` untouched | §1.3 | — |
| 3 | §2 Weekly Top-250 discovery/rank | Dry-run curation succeeds; manifest embeds `video_cdn_url` + `size_bytes` + GitHub Pages thumbnails | §1.1, §1.3 | — |
| 4 | §2 1-tap bulk caching + ~3 GB purge-first | Prompt appears on new week; old week purged before download; atomic `.tmp` promotion; ready/failed counts | Unit (coordinator) via §1.4 | M1 |
| 5 | §2 Offline MRT playback | Cached reels play with zero buffering in airplane mode | — | M2 |
| 6 | §2/§6 Bookmark tiers 1→1.5→2→3 | Vault → active-week URL (silent fall-through) → GitHub S3 stream → web fallback; offline badge | Unit via §1.4 (tiers 2/3 resolve) | M3 |
| 7 | §3 Decision log (9 rows) | Pre-cache ≤48 h, ref-only bookmarks, wipe-on-new-week, auto-prompt, dedicated manifest | §1.1, §1.3 | M1, M2 |
| 8 | §4 Offline/online matrix | Cached=offline OK; uncached=download-required; old bookmark offline=honest badge; metadata instant | — | M2, M3 |
| 9 | §5 No feed banner/pill; 9:16 covers; sort persists; grayscale; zero Cloudflare prompts | Visual + persistence checks | UI tests via §1.4 | M6 |
| 10 | §5 Single-repo GitHub sync (`bookmarks.json` + `thumbnails/` + Release assets) | Sync round-trip; release auto-create; presence-check skip | Unit via §1.4 | M4 |
| 11 | §5/§6 LRU cap (25/50/100/unlimited, default 50) | Uploaded-first eviction; bound-slot protection; thumbnails + metadata preserved | `testAudit013…Prioritization` via §1.4 | M5 |
| 12 | §5 JSON export/import + new-device restore | Base64 thumbnails round-trip; repo+PAT entry restores JSON + thumbnails | — | M4 |
| 13 | §6 Silent background sync (add/remove, foreground, sheet-open) | Coalescing; cellular gate (metadata yes, video no) | `…Coalescing`, `…CellularGate` via §1.4 | M4 |
| 14 | §6 Honest completed states | Census-derived "All reels…" + filter-empty branch in both sheets | `testAudit003…` via §1.4 | M1 |
| 15 | §6 60 FPS feed swiping | Zero main-thread disk decode on scroll path | Static: 0 `contentsOfFile` in Feed | M6 |
| 16 | §6 Dead-code cleanup + zero warnings | No Resolver/Banner/Cobalt; clean strict-concurrency build | §1.2, §1.4 (errors on warning) | — |

---

## 3. Manual Acceptance Checklist (physical iPhone via compiled IPA)

Setup: install the CI-built `InstagramDigestDirect.ipa` (Apple Configurator /
TestFlight / direct install per local signing), launch with Wi-Fi on, and
confirm the active week loads from `data_direct.json`. Each item is PASS/FAIL —
no partial credit.

### M1 — 1-tap bulk download + honest completed states (Req. 4, 7, 14)

1. [ ] New-week prompt: with a fresh week present, the home screen shows the
   prominent "Download Week for Offline" action.
2. [ ] Tap it: previous week's videos are purged first (device storage drops
   before it climbs — check Settings → Storage or the app's byte readout),
   then the batch downloads with live progress, concurrency badge (1x/3x), and
   ready/failed counts.
3. [ ] Partial batch: with some reels cached, `DownloadAllSheet` reads "N reels
   ready to download (M already cached)" and `Start Download` fetches only pending.
4. [ ] Full batch: with all cached, sheet shows "All reels downloaded and ready
   offline!", `Start Download` hidden, single prominent `Done`.
5. [ ] Filter-empty honesty: watch every reel (or enable "unwatched only" with
   all watched) while nothing is cached → sheet shows "Nothing pending under
   current filter (0/N cached on disk)", `Start` hidden, `Done` shown — and
   NEVER the "All reels downloaded" string.
6. [ ] Settings row mirrors census: "All Reels Downloaded (N/N)" green when
   complete, "(M pending)" otherwise; tapping opens `DownloadAllSheet`.

### M2 — MRT commute offline playback (Req. 5, 7, 8)

1. [ ] After M1 completes, enable airplane mode (or ride underground): every
   cached reel in the feed plays instantly, zero buffering, seekable.
2. [ ] An uncached reel shows "Download required"/retry affordance — no spinner
   of death, no crash.
3. [ ] Metadata/captions/thumbnails render instantly offline.

### M3 — Bookmarks playback across all 4 tiers (Req. 6, 8)

Prepare one bookmark per tier: (a) cached locally, (b) active-week uncached,
(c) evicted but uploaded to GitHub Release, (d) web-only (no local, no asset).

1. [ ] Tier 1: bookmark (a) plays instantly offline; grid badge is green
   checkmark (`…_cached`).
2. [ ] Tier 1.5: bookmark (b) online plays via stored Meta URL (orange download
   badge); with an expired/stale URL it silently falls through to Tier 2 with
   no error dialog, no captcha, no hang.
3. [ ] Tier 2: bookmark (c) online streams from the GitHub Release S3 redirect
   (cyan cloud badge `…_github`); second play starts faster (memory cache).
4. [ ] Tier 3: bookmark (d) online shows clean "Open on Instagram" (safari badge
   `…_web`); offline it shows "You're offline — this bookmark isn't downloaded".
5. [ ] No Cobalt/captcha/resolver UI appears anywhere during M3.

### M4 — Silent GitHub sync + restore (Req. 10, 12, 13)

1. [ ] Enter repo + PAT (with stray whitespace pasted around the PAT — must
   still work per AUDIT-008) → manual "Sync Bookmarks Now" succeeds; missing
   `v{year}` release is auto-created; uncached-but-local videos upload; already
   present assets are skipped, not duplicated.
2. [ ] Silent triggers (no modal, no spinner blocking UI): add a bookmark →
   background sync; remove a bookmark → tombstoned and removed from
   `bookmarks.json` on next sync (never resurrects = zombie check);
   foreground the app → sync; open `BookmarksSheet` → sync.
3. [ ] Cellular gate: on cellular data (or forced expensive network), metadata
   sync proceeds but video uploads defer; on Wi-Fi the deferred uploads complete
   without duplicates or phantom `video_asset_name` entries in `bookmarks.json`.
4. [ ] Export → JSON contains `thumbnail_base64`; delete + import restores rows
   with offline thumbnails. New-device restore: fresh install + repo/PAT pulls
   `bookmarks.json` and all `thumbnails/` covers automatically.

### M5 — 50-reel local vault eviction (Req. 11)

1. [ ] Settings default is "Latest 50 videos"; switching to 25/100/Keep-All
   takes effect on next add/save.
2. [ ] With >cap cached videos, the oldest GitHub-backed videos evict first;
   never-uploaded videos survive ahead of uploaded ones; the currently playing
   reel is never evicted mid-playback.
3. [ ] Evicted rows keep metadata + portrait thumbnail + grid presence; only the
   `.mp4` is gone, and Tier 2 still plays them online.

### M6 — 60 FPS swiping + presentation hardening (Req. 9, 15)

1. [ ] Rapid vertical swiping through the full feed is butter-smooth with no
   hitches, stale-frame flashes, or black-screen stalls (cold slots render clean
   black).
2. [ ] No feed banner ("All 250 Reels Cached Offline"), no header pill.
3. [ ] All covers are crisp 9:16 portraits in grid and bookmarks; Chronological
   (Newest) sort reorders live and persists across relaunch; Grayscale Detox
   applies to feed and bookmarks; no Cloudflare/owner-key prompt appears anywhere.

---

## 4. Pass/Fail Shipping Criteria

**SHIP (declare permanent freeze) iff ALL hold:**

1. §1.1 pytest: 31/31 green on the tip commit.
2. §1.2 `swiftc -parse`: exit 0 on the tip commit.
3. §1.3 scoped pipeline checks green; production `data.json` untouched.
4. §1.4 CI green at the exact pushed tip SHA (unit + UI tests, warnings-as-errors,
   `InstagramDigestDirect.ipa` attached). Any warning or test failure revokes approval.
5. §3 manual checklist: every box in M1–M6 PASS on the CI-built IPA on physical
   iPhone hardware (no simulator-only sign-off for M2/M3/M6).
6. Zero new crashes, hangs, or data-loss events observed during §3 (bookmark
   metadata, thumbnails, and `bookmarks.json` round-trip intact).

**FAIL (block freeze, return to Maker as Stage 5 defect — no new audit cycle):**
any single criterion above unmet, any phantom `video_asset_name` in pushed
`bookmarks.json`, any zombie bookmark resurrection after unsave, any Cobalt/
captcha/resolver UI, any "All reels downloaded" string shown when census is
partial, or any main-thread scroll hitch attributable to disk decode.

---

*Stage 4e UAT plan complete. Audit certification: `.workflow/AUDIT_AND_REMEDIATION_REPORT.md`.*
