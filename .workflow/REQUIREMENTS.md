# Requirements Specification: Fix Upload/Deployment Failure & Decouple App from GitHub

## 1. Project Overview & Target User Anchor
- **Project Name:** Instagram Digest (v1.0 / v5.1.0)
- **Target User & Scale:** Single-person personal use (Kedarnath Reddy / `vkr1729`). Personal single-user architecture.
- **Primary Objective:** 
  1. Explain and remediate why GitHub credentials failed.
  2. Publish the already-available 2026-10-17 batch (250 reels already in R2 and `data/top100_digest.json`) without re-running scraping/downloads.
  3. Decouple app publication from GitHub: Cloudflare R2 is the primary manifest source (`/data.json`), GitHub Pages is secondary. If GitHub Pages deployment fails, the app continues uninterrupted.
  4. Push code changes to GitHub.
  5. Auto-shutdown the machine upon full completion.
- **Core Value Proposition:** Reliable app playback every week with R2 as primary manifest host; zero disruption from external GitHub outages or credential blips.

## 2. Functional Requirements (Scope Matrix)
- **Publishing Available Batch (No Reruns, Zero Deletes):**
  - Read `data/top100_digest.json` (250 videos for week `2026-10-17`).
  - Do NOT scrape, download, or transcribe.
  - Do NOT purge R2 videos for week `2026-10-17`.
  - Compile `site/data.json` and static viewer site.
- **App Decoupling & R2 Manifest Publication:**
  - In `storage_r2.py`, add `upload_manifest_to_r2(manifest_path, key="data.json")` to upload `data.json` directly to the Cloudflare R2 bucket.
  - Make `https://instagram-digest-media.kedarvreddy.workers.dev/data.json` the primary manifest URL in `DigestDataService.swift`, with GitHub Pages as a fallback.
  - In `main.py`, publish `data.json` to R2 first so the app immediately gets the new week.
  - Make GitHub Pages deployment non-fatal for app operation: if git push to `gh-pages` fails, log a warning and alert, but DO NOT abort the pipeline if R2 publication succeeded.
- **GitHub Credential Cleanup & Robust Routing:**
  - Remove dead expired token from `~/.bashrc` lines 179-180 (`GITHUB_TOKEN` and `GITHUB_PERSONAL_ACCESS_TOKEN`).
  - In `site_builder.deploy_to_gh_pages()`, handle git authentication cleanly: support `GH_PAGES_PAT` / `GITHUB_PAT` via environment, or clean credentials without shadowing.
  - Support pushing code to `fork` and `origin`.
- **State & Persistence:**
  - Update `data/deployed_week.json` to `{"run_date": "2026-10-17"}` once manifest is published.
  - Update `data/last_run.json`.
- **System Action:**
  - Auto-shutdown machine upon 100% completion of all 5 stages.

## 3. Interview Record & Decision Log
| # | Functional Question | Recommended Approach | Evaluated Alternatives | User Decision / Rationale |
|---|---------------------|----------------------|------------------------|---------------------------|
| 1 | Target Persona & Scale | Single-user personal CLI/app | Multi-tenant SaaS | Confirmed: Personal single-user |
| 2 | Re-running Scrape | Do NOT rerun anything; publish available batch | Re-run full sync | Confirmed: Publish available batch |
| 3 | GitHub vs App Dependency | Decouple: R2 is primary manifest host for iOS app, GitHub Pages is secondary | Keep GitHub Pages as single point of failure | Confirmed: "App is primary and github is secondary, even if github fails, app should continue as is" |
| 4 | GitHub Credential Failure | Expired token in `~/.bashrc` + active account mismatch (`vkreddy1729-ops`) | Blame network | Root cause identified & documented |
| 5 | Post-Completion Action | Auto-shutdown upon 100% completion | Keep machine on | Confirmed: Auto-shutdown |

## 4. Frontier Probing Insights & Scope Gaps Addressed
- Addressed Q1 & Q2: `~/.bashrc` token removal + `site_builder.py` PAT / auth clean-up.
- Addressed Q3: Recovery-run invariant: ZERO R2 purges when publishing banked batch.
- Addressed Q4: R2 integrity gate verifies 250 banked videos in `videos/2026-10-17/` before manifest publication.
- Addressed Q5: R2 manifest is published first; GitHub Pages push is secondary; auto-shutdown only on full verification.
