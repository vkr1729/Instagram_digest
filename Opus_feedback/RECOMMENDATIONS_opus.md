# Recommendations — Instagram Digest

These are seven enhancements, highest value first, for a project with one owner, one laptop and one iPhone. Each one is judged by what it adds for you against what it costs to maintain.

They build on the fixes in `REVIEW_AND_BUGFIXES.md`. They propose no new services, no multi-user machinery and no dashboards nobody will look at.

**How the app changes get verified:** everything that touches the app can be verified by the existing macOS workflow (`.github/workflows/build-ipa.yml`), which runs unit tests and XCUITest before building the IPA. None of them needs a local Xcode.

**What's already been decided:** you declined a sleep timer and a playback-health dashboard in `f09f94e` ("new surfaces without a proven need"). That still holds, and neither is re-proposed here.

| # | Proposal | Area | Effort |
|---|----------|------|--------|
| 1 | Make CI prove that playback actually works | App tests | M |
| 2 | Show the new week without a force-quit | App | S |
| 3 | Back up every bookmark, and say when one isn't | App | S–M |
| 4 | Offline mode that plays only downloaded reels | App | S–M |
| 5 | One account-safety circuit breaker | Pipeline | M |
| 6 | Check the deploy the way the phone sees it | Pipeline | S |
| 7 | CI syntax check for rarely-run shell scripts | CI | S |

---

## 1. Make CI prove that playback actually works (M)

**Problem**

CI is the only place the app is ever checked, yet a completely dead player passes it today:
- The UAT fixture (`Resources/data.json`, week `2026-09-14`) streams from R2 keys that the 8-day retention has almost certainly purged. Every reel fails in CI, and the pool skips through them in the background (review P2-9).
- No XCUITest checks that playback time moves.
- Unit tests run inside a live host app that fetches the real manifest and drives the same `AVPlayerPool.shared` (review P2-8).
- Their test URLs 404, so the failure ladder moves the pool mid-test.
- The last four commits were all test repairs (`30255d7`, `eaeda47`, `d953542`, `dd62132`). At least one of them, `30255d7` "wait for slotCurrent content instead of asserting slot identity", is the timing symptom this proposal removes.

**Proposal** (one small asset and about 60 lines)

1. **Add a test clip.** Add `Resources/uitest_clip.mp4`: a 60-second, 64×64 black video with a silent track, about 30 KB. Generate it once on the laptop, which already has ffmpeg:

   ```bash
   ffmpeg -f lavfi -i color=c=black:s=64x64:r=10 -f lavfi -i anullsrc=r=22050:cl=mono -t 60 -c:v libx264 -tune stillimage -pix_fmt yuv420p -c:a aac -b:a 16k -movflags +faststart Resources/uitest_clip.mp4
   ```

   At 60 seconds it never auto-advances during a test, so rank-label assertions stay stable.
2. **Seed it for UI tests.** Under `-ui-testing`, after the existing cache wipe (`InstagramDigestApp.swift:15-18`), copy the clip into `MediaCache/<fixture week>/<id>.mp4` for the first 5 fixture reels. Playback then goes through the real local path (`LibraryPathResolver.isLocalFileAvailable`), with no network involved.
3. **Expose what's playing.** Add two accessibility values:
   - on the hairline progress bar (`ReelCardView.swift:145-159`): `.accessibilityIdentifier("PlaybackProgress").accessibilityValue(String(format: "%.1f", currentTime))`;
   - `"playing"` or `"paused"` from `pool.isPlaying` on the same element.
4. **Add three XCUITests:**
   - `testPlaybackClockAdvances`: the value increases within 3 s.
   - `testTapPausesClock`: after a tap the value stops changing and reads "paused".
   - `testBookmarkPlayerPlays`: save a reel, open Bookmarks, tap the tile, and check that the overlay appears and its clock advances. This needs the same accessibility value on the overlay.
5. **Make the rotation tests deterministic.** In `EngineTests`, build `ReelItem`s with `Bundle.main.url(forResource: "uitest_clip", withExtension: "mp4")!`. The memberwise init accepts file URLs, so there is no network and no failure ladder, and `testSlotRotation*` becomes deterministic.

**Why it pays off:** every app fix, including this review's P1-1 to P1-4, becomes something CI can confirm. The flaky-test churn stops. It all runs in the existing `Run Unit Tests` and `Run Deep UAT` steps.

## 2. Show the new week without a force-quit (S)

**Problem**

The manifest loads once per process (`.task { loadManifest() }`, `InstagramDigestApp.swift:405-407`). A modern iPhone keeps a suspended app alive for days, so Saturday's digest stays invisible until you force-quit. There is no pull-to-refresh. Leave the app suspended for more than 8 days and it streams URLs that R2 has already purged. The manifest's `generated_at` is decoded (`DigestManifest.swift:96-107`) but never shown.

**Proposal**

1. **Refresh quietly on foreground.** On `scenePhase == .active`, if the last successful fetch was more than 3 h ago, fetch the manifest quietly.
   - If `weekId` differs, run the existing rollover path (`loadManifest()`).
   - Otherwise do nothing, so you are never interrupted mid-reel.
2. **Show freshness.** Put a one-line freshness caption in the Grid sheet title, for example "Week of Sep 26 · updated 2 h ago", built from `generatedAt`.
3. **Keep the decisions testable.** Put the two decisions in `WatchedRules` as pure functions:
   - `shouldRefresh(lastFetch:now:)`
   - `isNewWeek(current:fetched:)`

**Verification:** unit tests for both pure functions. One XCUITest sends the app to the background and back (`XCUIDevice.shared.press(.home)`, then `app.activate()`) and asserts that the same rank badge is still showing, which proves a foreground event never disturbs playback.

**Why it pays off:** the weekly habit just works. It's also how you'd notice, from your phone, that a deploy never landed (review P0-1).

## 3. Back up every bookmark, and say when one isn't (S–M)

**Problem**

Bookmarks are the only data you keep long-term, and their durable R2 copy is best-effort:
- `saveRemoteBookmark` is fire-and-forget, and its result is thrown away (`BookmarkController.swift:85`).
- If you're offline, chose "Later" on the owner-key prompt, or hit a 5xx or 413 (review P2-11), the Worker never copies the reel to `bookmarks/`.
- The local row then still points at `videos/<week>/…`, which the pipeline purges within about 8 days. If the offline copy is later evicted by the 1.5 GB LRU, or was never made, the bookmark is gone and nothing tells you.
- The owner-key alert also pops on *every* new bookmark until you enter a key (`InstagramDigestApp.swift:757-764`).

**Proposal** (reuses patterns already in the codebase)

1. **Track pending backups.** Keep a pending set in UserDefaults, `ig_digest_pending_remote_bookmarks`, built like the existing tombstones (`MediaCacheManager.swift:14-33`).
   - Insert on `BookmarkController.add` and remove on a 2xx reply. The Worker already replays duplicates safely (`worker.js:266-275`).
   - Remove on unsave as well.
2. **Retry automatically.** On launch and foreground, retry the pending entries if a key is set.
3. **Show what isn't backed up yet.** In the Bookmarks grid, show a small `icloud.slash` badge on pending tiles (accessibility ID `BookmarkPendingBadge_<i>`). Show a header line "N not backed up" only when N > 0.
4. **Ask for the owner key once.** Prompt a single time (flag `owner_key_prompted`); afterwards the key is only reachable from "Link Key" in Bookmarks.

**Verification:**
- A unit test with a `URLProtocol` stub that returns 500, then 200, and checks that the pending set drains.
- An XCUITest that bookmarks with no key (the CI default), opens Bookmarks, and asserts that `BookmarkPendingBadge_0` exists.

**Why it pays off:** a bookmark can no longer die silently, and there is no new infrastructure.

## 4. Offline mode that plays only downloaded reels (S–M)

**Problem**

Offline, the feed walks into reels you haven't downloaded. Each one fails twice before it's skipped, and even with review P1-2 fixed you'd watch the pager fly past dead cards. A `Reachability` helper already exists (`InstagramDigestApp.swift:879-904`) but nothing uses it.

**Proposal**

1. **Filter while offline.** When `NWPathMonitor` reports no connection, narrow the playlist to reels with `LibraryPathResolver.isLocalFileAvailable`. Use the same `setReels` mechanism the category filter uses, and keep the current reel's position.
2. **Say so.** Show a chip in the category bar, for example "Offline · 87 downloaded".
3. **Restore on reconnect.** Bring back the full list when the connection returns.

It's one derived filter with no new state machine. The offline predicate is a pure function, `offlinePlaylist(items:isLocal:)`, so it can be unit-tested.

**Verification:**
- A unit test for the pure function.
- An XCUITest with a launch argument, `-ui-testing-offline`, that forces the offline branch, using §1's 5 seeded clips. It asserts the chip text and that paging stays within the 5 local reels.

**Why it pays off:** watching on the train or plane, the reason Download All exists, becomes seamless.

## 5. One account-safety circuit breaker (M)

**Problem**

Block and challenge detection is scattered across about 12 call sites: profile loads, reel pages, media-info, feed side tabs, yt-dlp, and expand. It keeps missing some of them: review P0-2, P1-9, P2-13, P2-14 and P2-28 are all "this path didn't notice the wall". The Instagram account is the one thing in this project that can't be replaced.

**Proposal** (about 40 lines in `extractor.py`, plus tests)

1. **One flag.** Add a module-level `threading.Event` with two helpers: `trip_gate(reason)` sets it, and `check_gate()` raises `InstagramChallenged` if it's set.
2. **Check it everywhere.** Every function that sends a request to Instagram calls `check_gate()` first.
3. **Trip it everywhere.** Every detector calls `trip_gate`:
   - URL markers;
   - a media-info 401 or `feedback_required`;
   - yt-dlp stderr containing `login required` or `rate-limit`;
   - N consecutive reel pages with block markers and no reel data.
4. **Reuse the existing abort.** `main.py`'s existing banked-abort handlers catch that single exception.
5. **Reset per run.** Reset the flag at the start of each `run_full_sync` and `run_expand`, because the dashboard server process lives for days.

**Verification:** a test that trips the gate and asserts every public extractor entry point refuses to run.

**Why it pays off:** one place to reason about, one test, and later fixes stop being whack-a-mole.

## 6. Check the deploy the way the phone sees it (S)

**Problem**

Success is currently declared on `git push`'s exit code, and at the moment not even that (review P0-1). A GitHub Pages build can lag or fail after a successful push. The health email only checks that Pages returns HTTP 200.

**Proposal**

1. **Poll after pushing.** After a successful push, poll `PAGES_BASE_URL/data.json?cb=<ts>` every 30 s for up to 10 minutes until `run_date == week_id`.
   - On timeout, call `_alert_sync_abort("pages not serving new week", …)`.
   - Write review P1-5's `deployed_week.json` only once this check passes.
2. **Report it.** The weekly health email gains one line: "Pages serves week X (generated Y)".

**Why it pays off:** it answers the only question that matters, "did my phone get this week?", automatically, with about 20 lines.

## 7. CI syntax check for rarely-run shell scripts (S)

**Problem**

The desktop installer quietly stopped parsing (review P2-22), and nothing noticed. The shell scripts run weekly, at login, or once a year, and `python-ci.yml` never checks them.

**Proposal:** one step in `python-ci.yml`:

```yaml
      - name: Shell syntax
        run: |
          for f in *.sh scripts/*.sh; do bash -n "$f"; done
          shellcheck -S error *.sh scripts/*.sh
```

`shellcheck` is preinstalled on `ubuntu-latest`.

**Why it pays off:** five lines, no upkeep, and it catches the kind of breakage you only find out about on a Friday night.

---

## Deliberately not proposed

- **Sleep timer and playback-health dashboard:** you declined both, and there's still no evidence you need them.
- **Background `URLSession` for Download All:** genuinely useful for a 2 GB batch, but it needs an app-delegate relaunch path that neither XCUITest nor unit tests can exercise in CI. Revisit only if keeping the app open while downloading becomes a real annoyance.
- **Hash-keyed R2 migration, multi-device watched-state sync, a Keychain migration for the owner key, a PWA DOM recycler:** each costs more upkeep than it's worth to one person. The owner-key trade-off is noted in the review under "Optional hardening".
- **More pipeline telemetry and dashboards:** §5 and §6 give sharper answers with fewer moving parts.
