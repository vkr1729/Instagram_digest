# UAT Plan — Direct-Meta Stream (Stage 4e, Independent)

Date: 2026-10-25 (UTC) · Branch: `arch/direct-meta-stream` · Scope: `.workflow/REQUIREMENTS.md` (§2 scope matrix, §3 decisions Q1–Q9, §4 offline matrix, §5 addendum) + `.workflow/AUDIT_AND_REMEDIATION_REPORT.md` (Stage 4d, 88 findings: gates (a)–(g))
Scale anchor: single-person personal use (Kedarnath Reddy) on iPhone, iOS 17+. No multi-user, no enterprise gates, no live-Instagram-gated CI.

Supersedes: the 2026-10-24 UAT plan previously in this file (preserved in git history). This revision adds: Stage 4d gate-entry criteria, the AUDIT-027 storage-architecture decision fork, stale-manifest (070) and sync-progress (079) acceptance cases, and a concurrency soak for the inspection-only thread-safety residual.

## 1. Independence principles & safety rails

- Black-box first: verify via observable surfaces — served `data_direct.json`, built `site/`, CLI exit codes + stdout, on-device playback behavior, badges, storage used. Assert on internals only where the contract names them (`DIRECT_STREAM_MODE`, `MIN_DEPLOY_ITEMS`, `data_direct.json`, `BookmarkBadge_<i>_<state>`).
- Never run bare `python main.py` or `python main.py --sync` (full production sync: real downloads, R2 quota, Pages deploy, durable state). Allowed pipeline probes only:
  - `.venv/bin/python main.py --dry-run --limit-per-creator 1 --days-back 1`
  - `.venv/bin/python main.py --build-only`
  - `.venv/bin/python main.py --expand 3 --dry-run`
- After any interrupted run: `rm -f data/sync_progress_*.json data/expand_checkpoint_*.json data/expand_progress_*.json`. Tripwire: no new files under `data/` and no `data.json` byte change after any UAT probe (compare `sha256sum data/seen_reel_ids.json data/last_run.json` before/after where applicable).
- Secrets: never inspect `.env`; use `.env.example` + `config.py` for schema. No PAT or token paste leaves the device (GitHub PAT is entered only in-app, never in chat/logs).
- Network mocking: pytest and CI stub the network. Only §6 (on-device) uses live network, and only the user's own phone on their own data/Wi‑Fi.
- Any red automated suite is a P0 regardless of matrix results. UAT may not start until Stage 4d gates (a)–(g) are green (see §2).

## 2. Entry criteria / environment

- Workstation: repo on `arch/direct-meta-stream`, `.venv` Python 3.12, Chrome session cookies present for discovery probes (extraction itself is not UAT-gated).
- iPhone: iOS 17+, app built from this branch (CI unsigned IPA or local Xcode run), native Instagram app NOT installed (per REQUIREMENTS §2).
- GitHub: private bookmarks repo available (`vkreddy1729-ops/ig-bookmarks` or custom) + a fine-grained PAT with contents read/write for sync/restore cases.
- Stage 4d gates (a)–(g) green: 032 test target compiles, 044 no-change regression test passes, 041 Xcode gate enforced, 027/070/071 fixed, 079 progress wired, `build-ipa.yml` green, pytest ≥211 green with clean `data/` tripwire.
- The AUDIT-027 fork must be resolved before §6 cases M-09/M-10: either (A) Release-Asset upload implemented, or (B) REQUIREMENTS amended to "resolver-only video restore; Release Assets deferred". UAT asserts whichever the branch implements — it must not assert the deferred path.

## 3. Automated test matrix

Run on the workstation (exact commands). All must be green; any failure blocks §6 and fails UAT.

| ID | Command | Covers (REQ / audit) | Pass bar |
|---|---|---|---|
| A-01 | `.venv/bin/python -m pytest tests/test_direct_stream_mode.py -q` | §2 pipeline toggle, manifest shape, MIN floor, byte preservation | 29 passed |
| A-02 | `.venv/bin/python -m pytest tests/test_direct_stream_mode.py tests/test_architectural_fixes.py tests/test_notifier.py tests/test_site_builder.py tests/test_cookie_free_uat.py -q` | §2 + §5 addendum regressions | 95 passed |
| A-03 | `.venv/bin/python -m pytest tests/test_upload_ordering.py tests/test_sync_resume.py tests/test_expand_resume.py tests/test_durability.py tests/test_teardown_final.py tests/test_fable_audit_fixes.py tests/test_stalled_job_safeguards.py -q` | Queue/resume/durability/teardown | 116 passed |
| A-04 | `.venv/bin/python main.py --build-only` | Static site compiles, no network, no deploy | Exit 0; `site/data_direct.json` valid JSON |
| A-05 | `.venv/bin/python main.py --dry-run --limit-per-creator 1 --days-back 1` | Discovery/ranking simulation | Exit 0; tripwire clean |
| A-06 | CI `build-ipa.yml`: `python-tests` job | Same as A-01 on runner | Green on branch |
| A-07 | CI `build-ipa.yml`: `xcodebuild test -only-testing:InstagramDigestTests` | Swift unit suite incl. 032 hermetic cases | Green; zero failures/skips |
| A-08 | CI `build-ipa.yml`: UI-test + release gates | Simulator boot, packaging fail-fast (088), Xcode pin gate (041) | Green; `Xcode 16` gate enforced |
| A-09 | `sha256sum` production `data.json` before vs after A-01–A-05 | INV-1 byte preservation | Identical hashes |
| A-10 | `grep -q 'Instagram_digest/data_direct.json' Sources/InstagramDigest/Engine/DigestDataService.swift` | Manifest-URL contract (071) | Exit 0 (runs in CI too) |

Swift-side notes: A-07 is the authoritative close-out for every Swift compile/concurrency verdict that was inspection+probe-only on Linux (022/025/034, all P0 Swift legs). If CI cannot run (runner outage), UAT is BLOCKED — local `swiftc` probes are not a substitute for the `xcodebuild` gate.

## 4. Requirements traceability matrix

| REQ source | Requirement | Automated | Manual (§5/§6) | Ship-critical |
|---|---|---|---|---|
| §2 pipeline | Top-250 discovery/ranking; local download for validation; R2 bypass toggle | A-01, A-05 | — | Yes (toggle) |
| §2 pipeline | `data_direct.json`: fresh `video_cdn_url` + Pages thumbs + `size_bytes` | A-01, A-04 | M-01 | Yes |
| §2 pipeline | Pages host `…/Instagram_digest/data_direct.json`; production `data.json` untouched | A-04, A-09 | M-01 | Yes |
| §2 iOS | Auto-prompt / 1-tap "Download Week", live progress | A-07 | M-02, M-03 | Yes |
| §2 iOS | ~3 GB purge-before-download (no overlap) | A-07 | M-04 | Yes |
| §2 iOS | 48h fresh window; atomic `.tmp` promotion; `(week_id,reel_id)` keys; ready/failed queue | A-07 | M-03, M-05 | Yes |
| §2 iOS | Local-first playback, zero buffering offline | A-07 | M-06 | Yes |
| §2 bookmarks | Reference-only bookmarks; active-week local play | A-07 | M-07 | Yes |
| §2 bookmarks | Cobalt resolver for expired; honest offline badge | A-07 | M-08, M-09 | Yes |
| Q1–Q9 decisions | Pre-cache batch; dynamic scrape; Cobalt; wipe-on-new-week; bypass (no delete); 1-tap; side-by-side manifest; immediate purge; Cobalt+offline badge | A-01–A-08 | M-02–M-09 | Yes |
| §4 matrix | 4 item-states × offline/online behavior | — | M-06–M-09 | Yes |
| §5 UI hardening | No banner/pill copy; 9:16 thumbs; persisted sort; detox; zero Cloudflare prompts | A-07 | M-10, M-11 | Yes (prompts = P0) |
| §5 bookmarks store | Single repo; `bookmarks.json` + `thumbnails/`; Release Assets OR resolver-only (§2 fork) | A-07 | M-09, M-12 | Yes |
| §5 LRU cap | 25/50/100/Unlimited; video-only eviction, thumb+metadata kept | A-07 | M-13 | No (default 50 suffices) |
| §5 export/import | Base64 thumbs; offline restore | A-07 | M-12 | No |
| §5 new-device restore | Repo+PAT pulls manifest + thumbs automatically | — | M-12 | Yes |
| 070/079 fixes | Stale-manifest gate; sync progress | A-07 | M-05, M-12 | Yes |

## 5. Workstation manual probes (M-01)

- **M-01 served-manifest check (after any real weekly deploy, not during UAT itself):** fetch `https://vkr1729.github.io/Instagram_digest/data_direct.json` and confirm: HTTP 200, `run_date` matches the deployed week, `items` length ≥ MIN floor (150 at TOP=250), every item has a Meta-CDN `video_cdn_url` (no `r2.dev`/worker/local URLs), thumbs resolve under `/thumbnails/`. Separately fetch production `data.json` and confirm byte-identical to pre-deploy hash (INV-1). FAIL = any R2 URL in the direct manifest, count below floor, or production bytes changed.

## 6. On-device manual checklist (M-02–M-14)

Setup: fresh week available on Pages (or staging manifest), phone on Wi‑Fi, storage noted (Settings → General → iPhone Storage → app). Cases run in order; each states setup → action → expected → FAIL trigger.

- **M-02 new-week auto-prompt + 1-tap banner.** Setup: new `week_id` on Pages, week not downloaded. Action: launch app to feed. Expected: "New week available" alert with "Download Week (~3 GB)" / "Later"; prominent 1-tap banner under the category bar; tapping banner pauses the pool and opens the download sheet. FAIL = no prompt, no banner, or banner dead.
- **M-03 bulk download with live progress.** Action: start "Download Week". Expected: live progress (downloaded/total + bytes), per-item failures counted live (`failed` tally, not silent), resumable after app backgrounding; completion shows green ready state, not a re-download prompt. Interrupt once (kill app mid-queue, relaunch, resume) and confirm no duplicate rows and partial progress kept. FAIL = spinner with no numbers, lost progress on relaunch, or crash.
- **M-04 ~3 GB purge discipline.** Setup: note app storage before M-03. Expected: old week purged at download start (storage drops before it climbs); steady-state footprint ≈ one week (~2.5–3.5 GB), never two overlapping batches. FAIL = overlap ≥5 GB peak or old-week files lingering after new-week start.
- **M-05 stale-manifest gate (070).** Setup: point the app at a manifest with `generatedAt` >48h old (staging or device-clock advance, then revert clock). Action: start "Download Week". Expected: "Week links likely expired — re-sync from workstation" instead of queue start. FAIL = queue starts against expired links.
- **M-06 MRT airplane-mode playback (the commute case).** Setup: week fully downloaded; enable Airplane Mode. Action: pager through ≥20 reels incl. seeking; open grid; read captions. Expected: instant playback from local files, zero buffering/spinners, metadata instant; no "online required" for cached items; airplane toggle mid-playback never black-screens a cached reel. FAIL = any spinner, stall, or remote fetch attempt on a cached reel.
- **M-07 3-state bookmark badges (live-update).** Setup: online; bookmarks in all three states (cached / downloadable / online-only). Action: toggle states (download a bookmark video, purge, go offline) while BookmarksSheet is open. Expected: badges flip live with no sheet reopen (`BookmarkBadge_<i>_<state>`), and match reality (cached badge ⇒ plays in airplane mode). FAIL = stale badge or badge/reality mismatch.
- **M-08 older-bookmark resolver playback (online).** Setup: online; bookmark from a purged week (original CDN URL expired). Action: play it. Expected: Cobalt resolution → fresh MP4 plays in-app (no Instagram app installed); a genuinely unresolvable reel shows an honest error, never an infinite spinner. Repeat 3× for flake check. FAIL = black screen, fake ticking clock, hang, or external-app bounce.
- **M-09 older bookmark offline (MRT tunnel).** Setup: airplane mode; same purged-week bookmark. Expected: honest "Online required for older bookmarks" badge immediately; zero hang. If fork (A) from §2 (Release Assets implemented): asset-backed video still plays offline. FAIL = spinner, hang, or silent no-op.
- **M-10 feed presentation (addendum).** Expected: no "All N Reels Cached Offline" banner or "Offline · N downloaded" pill anywhere; grid + bookmark thumbs crisp 9:16 (no squish); Chronological (Newest) reorders instantly and persists across relaunch; Detox grayscale covers feed AND bookmarks; zero Cloudflare/Owner-Key prompts or worker calls in any flow. FAIL = any banned copy, prompt, or squished thumb.
- **M-11 player robustness.** Rapid-scroll 50+ reels; background/foreground mid-play; lock/unlock; Silent-switch on/off. Expected: no crash, no stuck audio, no stale N+1 auto-advance, queue position sane. FAIL = crash, leaked audio, or lost position.
- **M-12 GitHub sync + progress + restore (079/080/081).** Setup: PAT unset, then set. Expected: sync disabled on empty PAT; malformed repo (`bad repo`, `owner/`, `/name`) shows inline hint and never crashes; valid sync shows determinate per-thumbnail progress (downloaded/total, not just a spinner); cancel/dismiss mid-sync is clean; empty bookmarks state offers a GitHub-restore CTA; entering repo+PAT on a fresh install pulls `bookmarks.json` + all `thumbnails/` automatically. FAIL = crash, throw-less hang, indeterminate-only progress, or missing thumbs after restore.
- **M-13 LRU cap spot-check (default 50).** Set cap to 25, cache aggressively, confirm eviction removes only `.mp4` beyond cap while metadata + `.thumb.jpg` survive and the playing video is never evicted; set Unlimited, confirm no coercion to 50. FAIL = thumb/metadata loss or playing-video kill.
- **M-14 share/export hygiene.** Share a feed reel and a bookmarked reel (incl. cancel mid-download); export bookmarks, delete app data, re-import. Expected: structured, cancellable downloads; iPad-style popover safe (no crash on large screens if available); export embeds base64 thumbs and re-import restores offline display with zero network. FAIL = temp-file growth, silent export failure, or thumbs missing after import.

## 7. Concurrency soak (covers the inspection-only residual)

Stage 4d verified 022/025/034 by reasoning only. On device, run each 3× and log anomalies (attach screen recording on failure):

- **S-1 403-storm:** start bulk download on an aging manifest so refreshes fire; rapidly pause/resume; sticky user pause must survive refresh completions (no auto-resume after user pause).
- **S-2 badge race:** spam Retry on `loadManifest` while toggling airplane mode; feed must never show duplicate rows, blank spinners, or stuck badges.
- **S-3 double-start:** double-tap "Start Download" during `.preparing` and `.downloading`; second tap must be ignored (no restarted batch, no duplicate queue).
- **S-4 background kill:** kill the app at ~50% bulk progress twice; relaunch must resume from ledger with counts consistent (`ready + failed + pending = total`).

## 8. Pass/Fail criteria for shipping

- **SHIP (GO):** A-01–A-10 all green + M-01–M-14 all pass + S-1–S-4 clean (or anomalies triaged as non-ship-blocking with a filed follow-up) + `data/` tripwire clean + no new crash/hang observed. The user signs §9.
- **NO-SHIP (BLOCKED):** any red automated suite; any FAIL in M-02–M-09 or M-12 (core loop: prompt → download → purge → offline play → badges → resolver → sync); any Cloudflare prompt (M-10); any data.json byte change (A-09); any un-triaged crash. Exceptions: none — all P0/P1-linked cases are hard gates.
- **Accepted limitations (shipable with notes):** M-13 non-default caps beyond 25/50/Unlimited spot-checks; cosmetic-only M-10/M-14 nits; single-flake resolver failures in M-08 that pass on immediate retry with an honest error shown (log the flake).

## 9. Sign-off

| Gate | Result | Evidence | Date/sign |
|---|---|---|---|
| Stage 4d (a)–(g) (code + CI + pytest) | ☐ GO / ☐ BLOCKED | CI run URL + pytest transcript | |
| Automated A-01–A-10 | ☐ GO / ☐ BLOCKED | Command transcripts | |
| On-device M-01–M-14 | ☐ GO / ☐ BLOCKED | Checklist notes + recordings | |
| Soak S-1–S-4 | ☐ GO / ☐ BLOCKED | Anomaly log | |
| Final ship decision | ☐ SHIP / ☐ NO-SHIP | Owner: Kedarnath Reddy | |

— Independent QA Lead, Stage 4e, 2026-10-25.

