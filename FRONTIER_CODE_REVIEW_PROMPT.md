# Frontier Review Handoff: Instagram Digest — Bug Audit + Enhancements

Clone read-only. Do not run the live Instagram pipeline (needs private
Chrome cookies); static review + tests only. Do not request or use secrets.

```bash
git clone https://github.com/vkr1729/Instagram_digest.git
cd Instagram_digest && git checkout main
```

## Context

Personal, single-user project: one owner, one laptop (Ubuntu), one iPhone.
Weekly overnight job scrapes followed Instagram creators, ranks 250 reels,
uploads to Cloudflare R2, deploys a static site + Swift app feed. Stack:
Python pipeline (`main.py`, `extractor.py`, `ranker.py`, `storage_r2.py`,
`site_builder.py`, `recommendations.py`, `notifier.py`, `local_server.py`,
`cookie_exporter.py`), ops dashboard (`templates/dashboard.html`), and —
most importantly — the SwiftUI iOS app where all actual usage happens
(`Sources/InstagramDigest/`: playback engine `AVPlayerPool`,
`MediaCacheManager`, `AudioSessionCoordinator`, SwiftData models,
feed/bookmark views; `Tests/`: unit + XCUITest suites; XcodeGen
`project.yml`, built and UAT-tested via `.github/workflows/build-ipa.yml`
on macOS runners).

Tune everything for single-person use: favor small, robust, low-maintenance
fixes. Reject anything that adds multi-user machinery, enterprise
governance, scaling theater, or operational burden exceeding its value.

## Task 1 — Bug audit with fixes

Review the Python pipeline, dashboard backend/frontend, shell
orchestration (`run_weekly.sh`, `run_friday_overnight.sh`,
`resume_pending.sh`), AND the iOS app (`Sources/`, `Tests/`,
`project.yml`) — weight the app heaviest, since it is the only surface
the user ever touches: playback correctness and ghost-audio/teardown
discipline (`AVPlayerPool`), offline cache and storage-cap behavior
(`MediaCacheManager`, 1.5 GB bookmark cap, weekly rollover vs pinned
bookmarks), SwiftData model topology and concurrency, and UI behavior
against the XCUITest suite. For each bug report: location (`file:line`),
trigger, impact on the weekly run, and a concrete minimal fix (unified
diff or exact edit). Cover at minimum: session/cookie handling, checkpoint
and resume correctness, R2 quota/purge safety, atomic writes and locking,
dashboard API guards, and shell failure modes. Rank findings P0 (breaks the
weekly run or risks data/account) / P1 / P2. Skip style nits.

## Task 2 — Enhancements

Propose 5–10 enhancements that make the project more robust or more useful
for one person, each with: problem, proposal, effort (S/M/L), and why it
pays off at personal scale. Include app-side UX/playback proposals with
the user — not the pipeline — as beneficiary; any app change must be
verifiable through the existing macOS GitHub Actions workflow (unit +
XCUITest), never requiring a local Xcode install. Prefer fewer moving
parts over more features.

## Output

Write exactly two markdown files and print both paths at the end:

1. `REVIEW_AND_BUGFIXES.md` — ranked findings, each with its fix.
2. `RECOMMENDATIONS.md` — enhancement proposals, highest value first.

Verify every cited `file:line` against the clone before writing. State any
assumption explicitly rather than guessing.
