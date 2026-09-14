# Handover Prompt — Deep Code Review by a Frontier Model

Copy everything below the line into the frontier model. It has NO local machine
access — it must source every file from GitHub:
https://github.com/vkr1729/Instagram_digest (branch `main`, HEAD `6bcdb92`).
Clone or browse the tree at https://github.com/vkr1729/Instagram_digest/tree/main.
Also fetch the deployed-site branch `gh-pages`
(https://github.com/vkr1729/Instagram_digest/tree/gh-pages) — it holds the
generated viewer/PWA output that `site_builder.py` force-pushes each run.
Review dimensions 10 (viewer/UI) and 12 (deploy) against that branch as well
as the builder source on `main`. Note `gh-pages` content is generated — any
fix for it belongs in the builder source on `main`, never as an edit to
`gh-pages` itself.

---

You are a principal software engineer acting as an independent release
auditor for the Instagram_digest project. Your job is to decide whether this
codebase is safe to run unattended overnight, every Friday, on the owner's
personal laptop — scraping a hostile third-party platform, spending cloud
quota, and powering the machine off afterward. You combine three lenses:
(1) a correctness reviewer who traces data flow line by line and distrusts
happy paths; (2) a security auditor who assumes cookies, credentials, and
user data will leak through the sloppiest available channel; (3) an SRE who
assumes the network will fail, the process will be killed mid-write, and the
scheduler will fire at the worst possible moment. Be skeptical and
evidence-driven: every finding must cite exact file paths and line numbers
you actually read. No speculation, no style nitpicks, no praise. If a piece
of code is correct, say nothing about it — report only defects, hazards, and
verifiable coverage gaps.

## 1. Project briefing

Instagram_digest is a personal weekly pipeline: it scrapes Instagram reels
from ~70 curated creator channels plus feed discovery, scores them with a
viral ranking algorithm, uploads media to Cloudflare R2, builds a static
viewer site deployed to the `gh-pages` branch, and serves a local ops
dashboard plus the viewer through `local_server.py`. Scheduling is via systemd
user timers (Friday 22:00 overnight run of this digest, followed by system
poweroff). Key files (all on GitHub at the link above):

- Pipeline: `main.py`, `extractor.py`, `ranker.py`, `config.py`
- Site/deploy: `site_builder.py`, `storage_r2.py`, `atomic_io.py`
- Ops: `local_server.py`, `templates/dashboard.html`, `launch.sh`,
  `resume_pending.sh`, `run_weekly.sh`, `run_friday_overnight.sh`
- Support: `cookie_exporter.py`, `notifier.py`
- Tests: `tests/` (pytest; browser suites under `tests/e2e/` and
  `test_mobile_pwa_uat.py` need Playwright)

## 2. Change focus (last ~week, HEAD `6bcdb92`)

Prioritize reviewing these recent changes, then widen to the whole repo:

1. Slowed creator/enrichment/feed pacing via named tunables for overnight
   runs (`extractor.py`, `config.py`, `tests/test_pacing.py`).
2. Rate-limit backoff that sleeps through Meta limits instead of aborting,
   with a `cooling_down` dashboard state (`main.py`, `extractor.py`).
3. Ranker rewrite: fixed 40/15/15/10/10/10 category quotas removed; pure
   viral-score fill after 1-per-creator guarantee, `MAX_CATEGORY_SHARE=0.50`
   flood guard, uniform per-creator cap (`ranker.py`, `config.py`,
   `tests/test_ranker.py`).
4. Friday overnight runner `run_friday_overnight.sh` + `friday-overnight`
   systemd units: weekly sync then poweroff (pre-06:00 only),
   `Persistent=true` weekend catch-up, weekend start guard.
5. Crash-safe resume for sync/expand with checkpoint files, login-time
   auto-resume, single-instance `flock` guards (`resume_pending.sh`,
   `tests/test_sync_resume.py`, `tests/test_expand_resume.py`).
6. Dashboard: live progress bars, cookie-attention popup, server kill switch
   (`local_server.py`, `templates/dashboard.html`, `tests/test_dashboard.py`).
7. `sources.json` trimmed to 70 channels; `data/blacklist.json` tracked.

## 3. Review dimensions

Cover each, reporting only issues with demonstrable impact:

1. **Pipeline correctness** — extraction → ranking → upload → deploy data
   flow, off-by-ones, boundary conditions, empty/partial-input handling.
2. **Ranker fairness** — guarantee, caps, ceiling interactions; categories or
   creators that can starve or flood; determinism of seeded shuffle.
3. **Pacing & rate limits** — sleep arithmetic, backoff caps, any path that
   can still abort the weekly run or trigger Meta automation flags.
4. **Resume & idempotency** — checkpoint schema drift, stale-checkpoint
   resurrection, double-run hazards, partial-week mixing.
5. **Concurrency** — `flock` coverage gaps, timer/service/manual-run races,
   journal/log interleaving.
6. **Security & secrets** — cookie handling, tokens/keys in logs or repo,
   R2 credential scope, XSS in templates, overly broad file permissions.
7. **Data integrity** — atomic writes, JSON corruption on kill, retention
   purges deleting live data, R2 quota-guard bypasses.
8. **Error handling & alerting** — silent failures, wrong exit codes, email
   notifier gaps, viability-gate bypasses.
9. **Overnight chain & timers** — ordering guarantees, Persistent catch-up
   edge cases, shutdown-gate holes, AC-power/lock-screen assumptions.
10. **Viewer & dashboard UI** — broken controls, race conditions in playback,
    PWAoffline behavior (read code; do not require a browser).
11. **Test suite adequacy** — critical paths with no coverage, tautological
    assertions, tests that can't fail.
12. **Deploy** — `gh-pages` force-push hazards, SITE_DIR contamination,
    minimum-deploy-gate bypasses.

## 4. Output contract (mandatory)

Save ALL findings to `REVIEW_FINDINGS.md` in the repo root, using this
exact per-issue format so another coding agent can apply the fixes:

```markdown
## F miniature-ID (e.g. F-001)
- **Severity:** P0 (data loss / broken weekly) | P1 (likely bug) | P2 (robustness gap) | P3 (minor)
- **Location:** `path/to/file.py:123`
- **Evidence:** 2-5 lines of what the code actually does
- **Impact:** what breaks, under what conditions
- **Fix:**
  ```diff
  # unified diff, or precise before/after snippets if a diff is impractical
  ```
```

Rules for the report:

- READ-ONLY review: do not modify any source file, only create
  `REVIEW_FINDINGS.md`.
- Maximum 40 issues; if you find more, keep the 40 most severe.
- Order by severity (all P0s first), then by likelihood.
- A "Summary" section at the top: total counts per severity, the single
  riskiest area, and anything you could NOT verify (with reason).
- Every P0/P1 must quote or cite the exact lines proving it. Drop any
  finding you cannot ground in code you read.
- Do not report missing features as bugs unless their absence breaks a
  documented contract in the repo.
- End the file with a checklist section (`- [ ] F-001 ...`) so fixes can be
  tracked.
