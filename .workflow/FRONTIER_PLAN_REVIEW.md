# Frontier Plan Review — Decouple App from GitHub & Publish 2026-10-17 Batch

Date: 2026-10-20 (UTC) · Scope: `.workflow/REQUIREMENTS.md` + `.workflow/IMPLEMENTATION_PLAN.md` (R2-primary manifest, Pages-secondary, ship banked 250 without re-scrape)
Scale anchor: single-person personal use. One owner (`vkr1729`), one R2 bucket, one workstation. Prefer deterministic local fixes + file-local state over new infrastructure. Per taste: functional delivery over stringent gates; diagnose from live probes before building.
Note: this review supersedes the prior rebrand/anti-doomscroll review formerly in this file (preserved in git history).

Live probes done during review (do not re-argue without re-probing):
- `GET https://instagram-digest-media.kedarvreddy.workers.dev/data.json` → **404 `Video Not Found`** (not served).
- `GET .../videos/2026-10-17/01_officialgaurangadas_Deg9OwnqBrc.mp4` → **206** (videos served).
- `GET https://vkr1729.github.io/Instagram_digest/data.json` → **200, `run_date` 2026-10-10, 250 items** (Pages serves last week, not the banked week).
- Banked `data/top100_digest.json` = 250 items, `run_date` 2026-10-17; `site/data.json` already compiled for 2026-10-17 (250 items). `data/deployed_week.json` = 2026-10-10 (drifted, write-only — nothing reads it). `data/last_run.json` already = 2026-10-17 weekly.

## P0. R2 `/data.json` is not served — S3 PUT alone ships nothing

- **Pitfall:** The plan assumes `put_object(data.json)` ⇒ `GET {R2_PUBLIC_DOMAIN}/data.json`. Live probe disproves it: videos return 206 but `/data.json` returns 404 `Video Not Found`. That body string appears nowhere in `cloudflare/worker.js` in this repo, so the serving host is a *different* media worker (not `ig-digest-api`) whose routes cover `videos/*` (and `bookmarks/*`) but not `/data.json`. Precedent inside the repo confirms the pattern: `regenManifest()` (`worker.js:76-84`) writes `bookmarks/manifest.json` to R2, yet the `fetch` handler (`worker.js:385-411`) has **no route that serves it** — manifests written ≠ manifests served.
- Setting `ContentType`/`CacheControl` on the S3 object is necessary but not sufficient: if the media worker serves via `MY_BUCKET.get()` it must forward `httpMetadata` headers, and Cloudflare edge cache must bypass for the manifest (videos are `immutable, max-age=1yr`; the manifest must be `no-cache`). Otherwise the phone reads a stale week for hours despite `reloadIgnoringLocalCacheData` (which bypasses `URLCache`, not edge).
- **Fix:** Before any "app immediately live" claim: (1) find the media worker source (outside this repo?) and add an explicit `GET /data.json` route → `MY_BUCKET.get("data.json")` with `content-type: application/json, cache-control: no-store` + edge bypass; `wrangler deploy` it; (2) *then* `curl` the public URL for `run_date` + count. If the media worker cannot be changed, use the raw R2 public endpoint (`pub-*.r2.dev/data.json`) as the manifest URL instead — but then the app needs that host, not the workers.dev one. Do not upload the real manifest until the route is proven with a canary object (e.g. `data-canary.json` round-trip).
- **Sequencing:** This is a new Phase 0 dependency (Worker route + deploy + canary verify). It blocks Phase 1 verification ("verify HTTP 200 via public domain") and all of Phase 5. The plan currently lists zero Worker steps.

## P0. `build_site()` must stay pure — upload belongs in `main.py` orchestration

- **Pitfall:** Plan Phase 2 puts `upload_manifest_to_r2()` *inside* `build_site()`. `build_site` is a pure renderer called from `run_full_sync`, `run_expand`, `run_reconcile`, `_build_only`, and unit tests (`tests/test_site_builder.py` calls it with a tmp `SITE_DIR`). A network side-effect there means: unit tests need S3 mocks; `--build-only` (advertised as local compile) silently publishes to R2; expand/reconcile double-upload; dry-run-adjacent paths risk live mutation.
- **Fix:** Keep `build_site()` pure (writes `site/data.json` only). Add the tiny `upload_manifest_to_r2()` helper in `storage_r2.py`, call it from `main.py` orchestration (`run_full_sync` post-verify, `_build_only`/`_deploy_only` when `--deploy` passed) *after* the integrity gate. Add a regression test asserting `build_site` performs zero S3 calls.

## P0. Phase 3 (iOS) cannot unblock Phase 5 (this week's phone)

- **Pitfall:** The installed phone runs the *old* binary whose `defaultManifestURL` (`DigestDataService.swift:7`) is Pages-only with fallback chain network → disk cache → bundle — `fetchManifest(from:)` takes a **single URL**, there is no primary→secondary URL fallback today. Changing the Swift default helps only after `build-ipa.yml` CI builds, signs, and the user manually installs the new IPA (~90 min + manual step). The plan's "Primary Publish → app immediately live" is false for the current fleet.
- **Fix:** State explicitly: for the 2026-10-17 batch the phone still reads Pages, so **Pages deploy success is still P0 for this batch**; R2-primary is future-proofing for subsequent installs. Do not declare Pages "non-fatal and skippable" for this recovery run. Implement true dual-URL fallback minimally when the R2 route exists: try R2, on *network* error fall back to Pages (not on 4xx/decode — a corrupt R2 manifest must not mask as stale Pages), single 15 s timeout each, no parallel race. Old-cache/bundle fallback stays as-is (never-cache-empty guard at `:53-57` is correct, keep).

## P0. `deployed_week.json` ordering is inverted and the file is currently write-only

- **Pitfall:** Existing invariant (P1-5, `site_builder.py:649-654`): `deployed_week.json` = what Pages *poll-verified as serving*, written only after the 10-min `run_date` poll passes. The plan inverts this — diagram writes it right after R2 upload, Phase 2 writes "if R2 upload succeeds." Grep shows **nothing reads `deployed_week.json`** today (`main.py`'s JIT purger keys off `_persisted_digest_week()` from `top100_digest.json`, not this file), and its content (2026-10-10) already drifted from the live digest (2026-10-17), proving it is not authoritative. Redefining it as "R2-live" while anything later treats it as "Pages-served" poisons rollback/purge decisions.
- **Fix:** Do not write `deployed_week.json` on R2 upload. Write it only after a surface is *verified serving*: either keep Pages-poll semantics, or extend to `{r2_live_week, pages_served_week}` once the R2 route exists. Never gate shutdown on a write-only file. `last_run.json` already reads 2026-10-17 weekly — do not rewrite identical files to tick boxes.

## P1. Dependency sequencing is backwards — auth first, iOS last

- **Pitfall:** Plan order (storage → site → iOS → auth-cleanup → publish) verifies Pages-dependent steps before fixing the credentials they depend on. `deploy_to_gh_pages` (`site_builder.py:565-664`) inherits ambient git config + the expired `GITHUB_TOKEN` in `~/.bashrc`; every Phase 2/5 poll fails environmentally and gets misattributed to code. Phase 3 needs a code push to trigger CI, impossible while auth is broken.
- **Fix order:** (0) Worker `/data.json` route + canary (new); (1) shell auth fix — remove expired `GITHUB_TOKEN`/`GITHUB_PERSONAL_ACCESS_TOKEN` from `~/.bashrc`, `gh auth switch --user vkr1729`, `git ls-remote` pre-check (no code, per requirements-review Q1); (2) `storage_r2.upload_manifest_to_r2` helper + mocked unit test; (3) `site_builder`/`main.py` wiring with R2-verify → Pages-poll → write-state ordering; (4) publish + verify banked batch; (5) iOS dual-URL + CI. Record pre-push `gh-pages` SHA for manual rollback (`git push -f origin <sha>:gh-pages`).

## P1. "Pages non-fatal" needs an explicit exit-code + email contract

- **Pitfall:** `main.py:2404-2414` today treats Pages-push failure as alerted abort (exit 2, "already emailed"). REQUIREMENTS flips this to non-fatal when R2 is live, but the plan updates no callers: `run_weekly.sh` maps exit 2 → "preserved previous digest" and exit 0 → success email with `site_url=PAGES_BASE_URL` (`main.py:2447` still advertises the Pages URL even when Pages failed); `healthcheck.sh`/health-report "Pages serves week X" line will lie; `_deploy_only` return codes (0/1) vs sync-path (0/2) diverge; bookmark thumbnails (`THUMB_BASE_URL` = Pages prefix, `wrangler.toml:21`) and app `thumbnail`/`poster` fields (`site_builder.py:250,255`) remain Pages-bound — **"app depends exclusively on R2" overclaims**; stale Pages = broken thumbnails/share images even with a live R2 manifest.
- **Fix:** Define the matrix: R2-ok + Pages-ok → 0, full success email; R2-ok + Pages-fail → 0 with warning *only* once the R2 route + installed-fleet caveat (P0) are resolved, else keep exit 2 for this batch; email must say "app live via R2, Pages stale (thumbnails/share affected), resume: `main.py --deploy`". Update health-report to check both surfaces independently. For this batch, hold the existing abort-on-Pages-fail behavior (P0 §3).

## P1. Shutdown is owned by the wrapper, not `main.py`

- **Pitfall:** REQUIREMENTS "auto-shutdown upon 100% completion of all 5 stages" + plan Phase 5 "auto-shutdown" duplicates `run_friday_overnight.sh:106-137`, which already gates poweroff on time-window (before 06:00), pipeline-lock (P2-25 `flock` check), and TubeLM ordering. A `poweroff` inside `main.py` bypasses all three and can sleep the box before iOS CI/install or TubeLM runs.
- **Fix:** `main.py` never powers off; it exits 0/2 and the wrapper decides. Define "100%" to exclude manual iOS install. No new shutdown code.

## P2. Over-engineering to trim

- **PAT plumbing:** `gh`-active-account fix + bashrc cleanup is the primary (zero code). `GH_PAGES_PAT`/`GITHUB_PAT` via `config.py` + token-in-URL + redaction + temp-`.git` scrub is fallback-only; do not build both in one run. If built: `config.py` read via existing `.env` loader, scope `public_repo` (or fine-grained contents-write, single repo), `chmod 600`, scrub `x-access-token:[^@]+@` from logged stderr, extend `finally` (`site_builder.py:662-664`) to cover crash paths. `.gitignore` already covers `.env` (lines 2-3) — verify, don't rework.
- **Helper size:** `upload_manifest_to_r2(path, key="data.json")` ≈ 15 lines reusing `get_s3_client()` + single `put_object`/`upload_file` with `ContentType=application/json, CacheControl=no-cache, no-store, must-revalidate`; return public URL or `None`. No new retry/poll framework — caller verifies with GET. R2 root key `data.json` is already exempt from all purges (all scoped to `videos/` — `purge_previous_weeks_videos`, `purge_expired_r2_objects`, `purge_unreferenced_r2_videos`), document that, no new keep-set code (and never reintroduce `keep_weeks` per AGENTS.md §5).
- **Swift fallback:** minimal sequential fallback (above), no parallel-race, no new cache layer.
- **Test paths:** plan cites `pytest tests/test_storage_r2.py` — that file **does not exist** (actual: `tests/test_storage.py`, `tests/test_uat_suite4_storage.py`, `tests/test_site_builder.py`). Correct the paths; new tests: manifest ContentType/CacheControl via mock, `build_site` purity (zero S3 calls), deploy exit-code matrix, Swift `URLProtocol`-stubbed fallback (CI-gated, never on Linux).

## P2. Missing but cheap: rollback + manifest verification

- Snapshot the prior R2 manifest before overwrite (`GET data.json` → backup key or rely on git `site/data.json`) so a bad publish reverts with one PUT (R2 versioning is off — assume destructive overwrite).
- Strengthen verification beyond `curl | grep run_date`: `python -c` assert `run_date == 2026-10-17 and len(items) == 250`, spot-HEAD 5 video URLs (full 250-HEAD reuse `_verify_digest_r2_keys`, which correctly drops only on definite 404 per `main.py:301-306`), assert `Cache-Control: no-store` on the manifest response. Recovery invariant holds: zero R2 deletes on this path (no `purge_*` on build/deploy/reconcile-only), weekly full-sync purge mandate unchanged.

## Suggested dependency-safe order

1. Worker `/data.json` route + `wrangler deploy` + canary round-trip (blocks all R2-live claims).
2. Shell auth fix + `ls-remote` pre-check (blocks all push verification).
3. `upload_manifest_to_r2` helper + mocked tests (no network in unit tests).
4. `main.py` wiring (pure `build_site`; R2-upload → R2-GET-verify → Pages-push → Pages-poll → state-write), exit-code/email/healthcheck updates.
5. Publish banked 2026-10-17 (integrity gate: suffix-match `_verify_digest_r2_keys`, 250/250 clean ship; 150–249 ship+outbox-warn; <150 abort; `MIN_DEPLOY_ITEMS = int(TOP_DIGEST_COUNT*0.6)`, never hardcoded 150), snapshot prior manifest first.
6. iOS dual-URL fallback after web semantics frozen; CI-gated.
7. Shutdown decision stays in `run_friday_overnight.sh` only.

## Patches to apply to the two docs

- REQUIREMENTS §2: name the Worker route + deploy as a prerequisite; move `~/.bashrc`/identity fix to step 0; change "update `deployed_week.json` once manifest is published" to "once *verified serving*"; scope "Pages non-fatal" to post-R2-route + post-reinstall, keeping abort-on-Pages-fail for this batch; soften "zero disruption" to "no stranded states on the happy path; explicit abort + `main.py --deploy` resume otherwise"; add thumbnail/Pages-bound caveat.
- IMPLEMENTATION_PLAN: insert Phase 0 (Worker); move auth cleanup to Phase 1; remove upload-from-`build_site`; correct test file paths; define R2-ok/Pages-fail exit codes; delete `main.py`-owned shutdown; add manifest snapshot/rollback + `Cache-Control` edge-bypass check.
