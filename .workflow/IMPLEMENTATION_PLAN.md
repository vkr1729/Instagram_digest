# Implementation Plan: Decouple App from GitHub & Publish Available Batch

## 1. Architectural Blueprint
- **Runtime & Stack:** Python 3.12/3.14 (backend/pipeline), Swift 6 (iOS client), Cloudflare R2 (S3-compatible storage & public CDN), GitHub Pages (secondary web viewer).
- **Component Boundaries:**
  - **Primary Manifest & Media Host (Cloudflare R2):** `https://instagram-digest-media.kedarvreddy.workers.dev/` serves both MP4 videos (`/videos/<week_id>/<file>.mp4`) AND the manifest (`/data.json`). App consumption depends exclusively on R2.
  - **Secondary Web Mirror (GitHub Pages):** `https://vkr1729.github.io/Instagram_digest/` serves the browser static viewer. Deployment failures to GitHub Pages are non-fatal for app playback.
  - **iOS Client (`InstagramDigest`):** Primary manifest URL points to R2, with automatic fallback to GitHub Pages.
- **Pipeline Data Flow:**
  ```
  Ranked Batch (data/top100_digest.json)
       │
       ▼
  Site Compilation (site/data.json + site/index.html)
       │
       ▼
  Primary Publish: Upload site/data.json to R2 (data.json)  <── App immediately live!
       │
       ├─► Update data/deployed_week.json
       │
       ▼
  Secondary Publish: Deploy to GitHub Pages (gh-pages)
       │ (Non-fatal: Warning logged if Git/GitHub fails; App remains 100% operational)
       ▼
  Complete & Verify
  ```

## 2. Component Breakdown & Execution Order

### Phase 1: Core Foundation & Storage Engine
- **`storage_r2.py`**:
  - Add `upload_manifest_to_r2(manifest_path: Path, remote_key: str = "data.json") -> str | None`.
  - Sets `ContentType="application/json"` and `CacheControl="no-cache, no-store, must-revalidate"`.
  - Returns public URL `f"{config.R2_PUBLIC_DOMAIN}/{remote_key}"`.
  - Verification: Upload test payload and verify HTTP 200/206 via public domain.

### Phase 2: Functional Logic & Site Builder Decoupling
- **`site_builder.py`**:
  - In `build_site(...)`, call `storage_r2.upload_manifest_to_r2(config.SITE_DIR / "data.json")`.
  - In `deploy_to_gh_pages(...)`:
    - Ensure environment `GITHUB_TOKEN` from `~/.bashrc` does not corrupt git push.
    - Support `GH_PAGES_PAT` / `GITHUB_PAT` via `config.py` if present.
    - If push fails, log warning and return False without throwing.
- **`main.py`**:
  - In `run_full_sync` and `_build_only` / `_deploy_only`:
    - Upload manifest to R2.
    - If R2 upload succeeds, write `data/deployed_week.json`.
    - Treat GitHub Pages failure as non-fatal: log warning and notify, but do not exit code 2 if R2 is live.
- **Verification check:** Compile and publish with `--build-only`, confirm R2 serves `https://instagram-digest-media.kedarvreddy.workers.dev/data.json` with week `2026-10-17`.

### Phase 3: iOS Client Manifest Resolution
- **`Sources/InstagramDigest/Engine/DigestDataService.swift`**:
  - Update `defaultManifestURL` to `URL(string: "https://instagram-digest-media.kedarvreddy.workers.dev/data.json")!`.
  - Add secondary fallback URL `URL(string: "https://vkr1729.github.io/Instagram_digest/data.json")!`.
- **Verification check:** Verify unit tests for `DigestDataService` compile and pass.

### Phase 4: Environment & Git Auth Cleanup
- **`~/.bashrc`**:
  - Remove dead expired exports: `GITHUB_TOKEN` and `GITHUB_PERSONAL_ACCESS_TOKEN`.
- **Git routing**:
  - Clean git push routing to `origin` and `fork`.

### Phase 5: Publishing Available Batch & Verification
- Publish the available 2026-10-17 batch (zero re-scraping, zero R2 video purging).
- Verify `https://instagram-digest-media.kedarvreddy.workers.dev/data.json` serves `2026-10-17` with 250 items.
- Verify `data/deployed_week.json` is `2026-10-17`.
- Commit changes and push to GitHub.
- Auto-shutdown machine upon 100% completion.

## 3. Frontier Architectural Review & Enhancements
*(To be populated by Stage 2b frontier review)*

## 4. Verification & Guardrails
- **Automated Tests:** Run existing test suites (`pytest tests/test_storage_r2.py`, `pytest tests/test_site_builder.py`, `swift test` or swift compile check).
- **Network / Endpoint Sanity:** `curl -s https://instagram-digest-media.kedarvreddy.workers.dev/data.json | grep -o '"run_date":[^,]*'`.
- **Circuit Breaker Rule:** 2-patch limit before calling frontier model.
