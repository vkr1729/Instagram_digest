# Review & Bug Fixes — Instagram Digest

An independent audit of `vkr1729/Instagram_digest`, branch `main` at **`dd62132`** (2026-09-26), using a fresh read-only clone.

**Totals:** 2 × P0 · 12 × P1 · 29 × P2. Within each severity, app findings come first.

---

## How this review was done

**Code reviewed**
- **iOS app:** I read every file in `Sources/` and `Tests/`, plus `project.yml` and `build-ipa.yml`, line by line.
- **Python pipeline, server, dashboard, shell, systemd and Worker:** four read-only reviewers audited these in parallel.
- **Verification:** I re-checked every candidate finding against the code before including it. Several were rejected or downgraded. Every `file:line` below was checked against the tree at `dd62132`.

**What was executed**
- **Python suite:** run on Windows 11 with Python 3.13, which is not the Ubuntu target. I used the CI selection from `python-ci.yml`: 482 tests collected.
  - With two local shims, 464 pass. The shims were `os.O_DIRECTORY` for the directory fsync and `PYTHONUTF8=1`.
  - The other 18 fail, all for platform reasons: 6 need Chromium, 7 are `fcntl`/flock lock tests, 3 check POSIX `0600` modes, 1 needs ffmpeg, 1 needs the desktop-launcher file, and 1 is a Winsock abort.
  - I saw no genuine failures.
  - However, the run left files in the clone's real `data/` directory, which became finding **P1-12**. I deleted them afterwards (`git clean -fdX`), and the clone is back to a fresh checkout.
- **Shell scripts:** `bash -n` on all of them.
- **Swift:** not built, because no macOS was available. The Swift fixes are written against the verified current text but have not been compiled. Each is small enough to verify through `build-ipa.yml`, the only Swift build path, since you have no local Xcode.

**Not done**
- I did not run the live pipeline, the dashboard server or any network call.
- I requested and opened no secrets. None exist in tracked files: `cloudflare/wrangler.toml` holds only names, IDs and URLs, with secrets set via `wrangler secret put`.

## How this relates to the existing review in the repo

The repo's own `REVIEW_AND_BUGFIXES.md` has 30 findings, all marked fixed in `f09f94e`. Most of those fixes hold. The ones that don't:

- **A6:** the fix dropped its `.cached` status flip. Now part of **P1-4**.
- **P1-6:** the run-kind gate is too broad and now retires work it should finish. See **P1-6**.
- **P2-11:** only partly fixed. See **P1-5**.
- **P2-15/16:** only partly fixed. See **P2-20**.
- **P2-21:** regressed; the installer no longer parses. See **P2-22**.
- **B29:** only partly fixed. See **P2-29**.

## Assumptions

1. **Runtime:** your Ubuntu laptop runs the pipeline from a `.venv` built as the README describes, without `dbus-python`. Comments at `main.py:674` and `extractor.py:193` say the same.
2. **Device:** iOS 17+ on your iPhone, with the IPA installed after you re-sign it.
3. **UAT fixture:** `Resources/data.json` is the `2026-09-14` digest. I assume its R2 objects have been purged, because `RETENTION_DAYS=8` (`config.py:111`) and the JIT purge keeps only the live and current weeks. I did not verify this, since I made no network calls.
4. **Audio option validity:** P1-1 relies on Apple's documented rules for audio session options. The fix comes with a CI test that confirms the diagnosis on the unfixed code first.
5. **Severity scale:**
   - **P0:** breaks the weekly run, makes it fail silently, or risks data or the Instagram account.
   - **P1:** a visible failure in the app, or a likely lost week or bookmark.
   - **P2:** narrower, latent or hygiene issues that still have real consequences.
   - Style nits are left out.

## Suggested landing order

1. **P1-12 first**, before running the suite on the laptop again. Then **P0-1 and P1-5 together** (deploy honesty and protecting the served week).
2. **P0-2 plus P1-8, P1-9, P1-10 and P1-6** (the account-safety cluster).
3. **App: P2-9 first**, because it changes what the XCUITests can see. Then P1-1, P1-2, P1-3 and P1-4 in one CI round.
4. **Everything else.**

---

# P0

## P0-1 · [Pipeline] A failed GitHub Pages push exits 0 and sends "Digest Ready"; the next run then deletes the week your phone is still playing

- **Location:**
  - `main.py:2166-2168`: `site_builder.deploy_to_gh_pages()`'s result is ignored.
  - The same pattern at `main.py:607` (reconcile), `main.py:2638` (expand), `main.py:2697-2698` (`_deploy_only` returns 0 regardless) and `scripts/topup_digest.py:329-330`.
  - `site_builder.py:603-612` never raises; it returns `False` on push failure or any exception.
- **Trigger:** `git push -f origin gh-pages` fails. Causes include an expired or rotated GitHub token (the classic unattended-HTTPS failure), a network drop or a GitHub outage.
- **What happens:**
  - The run calls `save_last_run_info(...)` (`main.py:2171`), sends `send_digest_email(... site_url=config.PAGES_BASE_URL ...)` (`main.py:2187-2197`) and exits 0.
  - `run_weekly.sh:49-50` logs success, so no alert fires.
  - The health report only checks that Pages returns HTTP 200, so it says OK.
- **Impact on the app:** the app keeps reading last week's `data.json` (`DigestDataService.swift:7`) while you are told the new week shipped.
- **The following week:** the JIT purge keeps only the week the *local* digest points at (`main.py:1973-1974`, see P1-5). It deletes the week Pages and the app are actually serving. Every reel you haven't downloaded stops playing, and success emails keep arriving.
- **Fix:** treat a failed deploy as an alerted abort. Exit 2 means "main.py already emailed" to `run_weekly.sh:51-56`.

```diff
--- a/main.py
+++ b/main.py
@@ -2166,5 +2166,12 @@
     # 9. Deploy to GitHub Pages (Viability & Minimum items gate)
     if deploy and not dry_run:
-        site_builder.deploy_to_gh_pages()
+        if not site_builder.deploy_to_gh_pages():
+            # Scrape + upload happened: keep the anchor, but never claim success.
+            save_last_run_info(week_id, since_timestamp=since_timestamp, kind=kind)
+            _alert_sync_abort(
+                "deploy failed",
+                "git push to gh-pages failed; Pages and the app still serve the previous week. "
+                "Fix the token/network, then run: main.py --deploy")
+            return 2
```

Apply the same check at the other call sites:

```diff
@@ -607 (reconcile)
-        site_builder.deploy_to_gh_pages()
+        if not site_builder.deploy_to_gh_pages():
+            _alert_sync_abort("deploy failed", "reconcile: git push to gh-pages failed")
+            return 2
@@ -2638 (expand)
-        site_builder.deploy_to_gh_pages()
+        if not site_builder.deploy_to_gh_pages():
+            _alert_sync_abort("deploy failed", "expand: git push to gh-pages failed")
+            return 2
@@ -2697 (_deploy_only)
-    site_builder.deploy_to_gh_pages()
-    return 0
+    return 0 if site_builder.deploy_to_gh_pages() else 1
--- a/scripts/topup_digest.py
+++ b/scripts/topup_digest.py
@@ -329,2 +329,4 @@
-        site_builder.deploy_to_gh_pages()
-        logger.info("Successfully deployed to GitHub Pages!")
+        if not site_builder.deploy_to_gh_pages():
+            logger.error("Deploy failed; digest saved locally. Run: main.py --deploy")
+            return 1
+        logger.info("Successfully deployed to GitHub Pages!")
```

- **Tests:** the existing `test_weekly_exit2_sites_always_alert_ast` sentinel already requires every new `return 2` to have an alert beside it, and this fix satisfies that.

## P0-2 · [Pipeline] Challenge and login walls are swallowed during enrichment, so the promised "abort on challenge" never fires there

- **Where the per-reel path fails:**
  - `extractor.py:1149`: `page.goto(reel_url, ...)` has no `_assert_not_blocked`.
  - `extractor.py:1178-1185`: a soft-blocked page returns `None`, which looks like "no data".
  - `extractor.py:1261-1262`: `except Exception` would swallow an `InstagramBlocked` anyway.
  - As a result, `extract_single_reel_metadata` can never raise `InstagramChallenged`. The abort handlers at `main.py:1627-1628` and `main.py:1648-1650` are dead code.
- **Where the media-info path fails:**
  - `extractor.py:1024-1033`: `_response_indicates_challenge` (`extractor.py:653-657`) only matches checkpoint and challenge words.
  - A `401 require_login`, a `400 feedback_required` or a "please wait a few minutes" throttle hits `logger.debug(...); continue`, and the loop carries on.
- **The isolated metadata tab in feed discovery:** `extractor.py:1755-1757` swallows every exception the same way.
- **Trigger:** the account gets checkpointed, or the session dies, during Pass-1 enrichment. That is plausible for the recently cut-over, trust-warming account.
- **Impact on the account:**
  - The media-info batch keeps making authenticated calls after Instagram has said stop, then returns everything unenriched.
  - The per-reel loop then visits every remaining shortlist reel, up to about `TOP_DIGEST_COUNT*2`, one Playwright page load each, with `ENRICH_PAUSE` of about 5 s (`main.py:637`) between them. That is up to an hour of hitting a gated account.
  - The run finally ends as a vague shortfall instead of the challenge email and banked abort. `ARCHITECTURE.md` §3.1 promises "the pipeline never challenge-loops".
- **Fix:** detect the wall by URL on every reel page, stop the API batch on login or feedback signals, and let `InstagramBlocked` reach the existing abort handlers.

```diff
--- a/extractor.py
+++ b/extractor.py
@@ -1149,1 +1149,2 @@
         page.goto(reel_url, wait_until="domcontentloaded", timeout=18000)
+        _assert_not_blocked(page, f"reel {shortcode or reel_url}")  # checkpoint/login redirect => raise
@@ -1261,2 +1262,4 @@
-    except Exception as exc:
+    except InstagramBlocked:
+        raise  # a wall is never a "try yt-dlp instead" condition
+    except Exception as exc:
         logger.debug("Playwright extraction failed on %s: %s; trying yt-dlp fallback...", reel_url, exc)
@@ -1031,3 +1033,10 @@
         if resp.status_code != 200:
+            body = resp.text[:500].lower()
+            if resp.status_code in (401, 403) or any(t in body for t in (
+                    "require_login", "login_required", "feedback_required", "please wait a few minutes")):
+                # Instagram said stop: never grind the rest of the batch.
+                raise InstagramBlocked(
+                    f"media-info HTTP {resp.status_code} on {sc}: {body[:80]}")
             logger.debug("media-info HTTP %d for %s.", resp.status_code, sc)
             continue
@@ -1755,1 +1766,5 @@
-                except Exception as meta_err:
+                except InstagramChallenged:
+                    raise
+                except InstagramBlocked as blk:
+                    raise CookieExpiredException(str(blk), partial=external_candidates)
+                except Exception as meta_err:
--- a/main.py
+++ b/main.py
@@ -1570,1 +1570,1 @@
-                    except extractor.InstagramChallenged as challenge_err:
+                    except extractor.InstagramBlocked as challenge_err:   # challenge OR login wall
@@ -1627,1 +1627,1 @@
-                        except extractor.InstagramChallenged:
+                        except extractor.InstagramBlocked:
@@ -1648,1 +1648,1 @@
-                        except extractor.InstagramChallenged as challenge_err:
+                        except extractor.InstagramBlocked as challenge_err:
```

- **Why this scope:** the soft-block heuristic at `extractor.py:1178-1185` stays as it is. Its marker strings also appear in captions. The URL and API-status signals above are the unambiguous ones.
- **Tests:** add a unit test in which a mocked page's `url` is `/challenge/...` and assert that `extract_single_reel_metadata` raises. Add another in which media-info returns 401 and assert that the batch raises.

---

# P1 — iOS app

## P1-1 · [App] The audio session category is rejected, so reels fall back to `.soloAmbient`, which the silent switch mutes

- **Location:**
  - `Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift:22-26`:

    ```swift
    setCategory(.playback, mode: .moviePlayback,
                options: [.allowBluetooth, .allowBluetoothA2DP, .allowAirPlay, .mixWithOthers])
    ```

  - The error is swallowed at `:27-29` ("Non-fatal, default system audio category remains").
- **Why it fails:** Apple documents `.allowBluetooth` (HFP) as valid only for `.playAndRecord` and `.record`, and `.allowAirPlay` as valid only for `.playAndRecord`. An incompatible option set makes `setCategory` throw (OSStatus −50), so the session keeps the default `.soloAmbient`.
- **Impact:** the app is designed around `.playback`, which is why `B8` and interruption handling exist. With `.soloAmbient`:
  - The Ring/Silent switch or Action-button silent mode mutes every reel.
  - Other apps' audio is interrupted rather than mixed, despite `.mixWithOthers`.
  - The route and interruption semantics differ from what the coordinator assumes.
  - The simulator has no silent switch, so XCUITests can't notice any of this.
- **Fix:** for `.playback`, A2DP and AirPlay routes are automatic, so pass only valid options.

```diff
--- a/Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/AudioSessionCoordinator.swift
@@ -19,12 +19,13 @@
     public func configureAudioSession() {
         let session = AVAudioSession.sharedInstance()
         do {
-            try session.setCategory(
-                .playback,
-                mode: .moviePlayback,
-                options: [.allowBluetooth, .allowBluetoothA2DP, .allowAirPlay, .mixWithOthers]
-            )
+            // .allowBluetooth / .allowAirPlay are playAndRecord-only: with
+            // .playback they make setCategory throw and leave .soloAmbient
+            // (muted by the silent switch). A2DP + AirPlay are automatic here.
+            try session.setCategory(.playback, mode: .moviePlayback, options: [.mixWithOthers])
         } catch {
-            // Non-fatal, default system audio category remains
+            // Non-fatal at runtime; a regression here is caught by
+            // EngineTests.testAudioSessionUsesPlaybackCategory in CI.
         }
     }
```

- **CI verification (unit test):** add this test to `Tests/InstagramDigestTests/EngineTests.swift`. Push it once *before* the fix: if it fails, the diagnosis is confirmed. After the fix it must pass.

```swift
@MainActor
func testAudioSessionUsesPlaybackCategory() {
    AudioSessionCoordinator.shared.configureAudioSession()
    XCTAssertEqual(AVAudioSession.sharedInstance().category, .playback,
                   "setCategory must not silently fall back to .soloAmbient")
}
```

- **Confidence:** medium-high. It rests on the documented option rules, and the test above settles it in one CI run.
- **Separate product decision:** `.mixWithOthers` means Spotify keeps playing under your reels and the app has no Now Playing entry. That is your call, not a bug.

## P1-2 · [App] The dead-reel auto-skip moves the player but not the pager: next reel's audio plays behind a black card, and Save/Share hit the wrong reel

- **Location:**
  - `Sources/InstagramDigest/Engine/AVPlayerPool.swift:755-758`: the second-strike skip calls `setCurrentIndex(currentIndex + 1)` directly.
  - The UI never learns about the move. The pager binds layers by `currentAttachedIndex`, which comes from `activeIndex` (`FeedPagerView.swift:466-481`).
  - The HUD, bookmark and share all read `pool.currentItems[activeIndex]` (`InstagramDigestApp.swift:170-172`, `:742-744`, `:797-799`).
- **Trigger:** any reel whose remote stream fails twice. The most common case is **offline with a partially downloaded digest**: every reel you haven't downloaded fails fast, strikes out and is skipped.
- **Impact:**
  - The visible cell keeps the demoted slot's layer, which shows black or a frozen frame, with reel *N*'s handle and caption on top. Meanwhile reel *N+1* (or *N+k* after a cascade) plays audio from an off-screen layer.
  - Save and Share act on reel *N*.
  - When reel *N+k* ends, `onAutoAdvanceToNext` jumps to `pool.currentIndex + 1`. The pager then leaps, and the reels it passes over are marked watched through `jumpToReel`.
  - This hits the scenario Download All exists for: watching on the train.
- **Fix:** route the skip through the UI, the same way auto-advance is routed.

```diff
--- a/Sources/InstagramDigest/Engine/AVPlayerPool.swift
+++ b/Sources/InstagramDigest/Engine/AVPlayerPool.swift
@@ -548,2 +548,5 @@
     public var onAutoAdvanceToNext: (@MainActor () -> Void)?
+    /// Dead-reel skip must move the pager too; otherwise the visible card,
+    /// HUD and Save/Share stay on the dead reel while the next one plays.
+    public var onSkipDeadReel: (@MainActor (Int) -> Void)?
@@ -755,4 +758,8 @@
             } else if currentIndex + 1 < currentItems.count {
                 // Second strike: skip dead reel and advance
-                setCurrentIndex(currentIndex + 1)
+                if let skip = onSkipDeadReel {
+                    skip(currentIndex + 1)
+                } else {
+                    setCurrentIndex(currentIndex + 1)
+                }
             }
--- a/Sources/InstagramDigest/InstagramDigestApp.swift
+++ b/Sources/InstagramDigest/InstagramDigestApp.swift
@@ -584,1 +584,7 @@
         }
+        pool.onSkipDeadReel = { nextIndex in
+            // Move the pager with the pool. A skipped (unplayable) reel is not
+            // "watched", so no predecessor marking here (unlike jumpToReel).
+            guard nextIndex < pool.currentItems.count else { return }
+            activeIndex = nextIndex            // pager scrolls + re-binds layers
+            pool.setCurrentIndex(nextIndex)
+        }
     }
```

- **Land with P2-9:** today every UAT fixture reel fails playback in CI (see P2-9). With this fix the pager would visibly follow the resulting skip cascade, and the rank-label assertions in the XCUITests would become racy.
- **Optional:** stop auto-skipping after about 5 consecutive dead reels and pause with chrome visible, so an offline session doesn't fly through the whole list.

## P1-3 · [App] "Keep offline" saves HTTP error pages as the bookmark video and leaks cap reservations when downloads fail

- **Location:** the remote branch of `MediaCacheManager.keepBookmarkOffline`.
  - `MediaCacheManager.swift:371`: `let (tempURL, _) = try await boundedSession.download(from: remoteURL)`. The response is discarded, so there is no status check.
  - `:362-363`: `estimatedBytes` is reserved.
  - `:371` throwing leaves that reservation in place.
  - `:396`, `:406` and `:410` release only `actualBytes`, even when `actualBytes < estimatedBytes`.
- **Trigger:**
  - Bookmark a reel you haven't downloaded (the normal streaming case) while the Worker or R2 returns 403, 404 or 5xx. This happens with a purged key, a Worker exception or rate limiting. `URLSession.download` *succeeds* on those codes and hands back the error body.
  - Separately, any timeout, dropped connection or task cancellation (unsaving quickly cancels the add task, `BookmarkController.swift:21`) leaks the reservation.
- **Impact:**
  - A roughly 1 KB XML or HTML error body is moved into `Bookmarks/{id}.mp4` and marked `.cached`, and the green checkmark shows.
  - `BookmarkPlayerOverlay.loadVideo` prefers any local file (`BookmarksSheet.swift:528-535`) and never falls back, so that bookmark plays **black forever**. `keepBookmarkOffline` returns early whenever the file exists (`:309`), so nothing repairs it.
  - Leaked reservations only clear on relaunch. Until then `ensureSpaceForBookmark` evicts real offline favorites to make room for bytes that don't exist. Evicted old-week reels may be impossible to download again once R2 purges them.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/MediaCacheManager.swift
+++ b/Sources/InstagramDigest/Engine/MediaCacheManager.swift
@@ -368,5 +368,21 @@
             let boundedSession = URLSession(configuration: boundedConfig)
-            let (tempURL, _) = try await boundedSession.download(from: remoteURL)
+            let downloaded: (URL, URLResponse)
+            do {
+                downloaded = try await boundedSession.download(from: remoteURL)
+            } catch {
+                // Timeout/offline/cancel: return the admission, or the cap
+                // shrinks until relaunch and evicts real offline bookmarks.
+                reservedBookmarkBytes = max(0, reservedBookmarkBytes - estimatedBytes)
+                throw error
+            }
+            let (tempURL, response) = downloaded
+            // URLSession "succeeds" on 403/404/5xx and returns the error body:
+            // never promote that to a "Saved offline" MP4.
+            guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
+                try? FileManager.default.removeItem(at: tempURL)
+                reservedBookmarkBytes = max(0, reservedBookmarkBytes - estimatedBytes)
+                throw CacheError.corruptedFile("HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1) for \(reelID)")
+            }
             let dest = bookmarkDestURL
             let actualBytes: Int64 = Self.diskFileSize(atPath: tempURL.path) ?? estimatedBytes
+            // What this admission holds in reservedBookmarkBytes from here on.
+            let held = max(estimatedBytes, actualBytes)
@@ -396 / -406 / -410 (move-failure, save-success and save-rollback paths)
-                reservedBookmarkBytes = max(0, reservedBookmarkBytes - actualBytes)
+                reservedBookmarkBytes = max(0, reservedBookmarkBytes - held)
```

- **Existing bad copies:** error-body copies already on the phone are `.cached`, so one tap of **Free Local Storage** in Bookmarks clears them.
- **CI verification (unit test):** use a `URLProtocol` stub that returns 404 with a 200-byte body. Assert that `keepBookmarkOffline` throws, that no file exists at `bookmarkFileURL`, and that `totalBookmarkBytes` is unchanged.

## P1-4 · [App] Unsave then re-save loses the offline copy; unsave then quit leaves an orphan that permanently uses up the 1.5 GB cap

- **Location:**
  - `MediaCacheManager.swift:309-318`: the file-already-exists path never sets `item.localStatus = .cached`. The prior review's A6 fix included that line; the landed version dropped it. It also never cancels a pending deletion.
  - `:497-502`: unsaving a reel that is still bound to the player is deferred into `pendingDeletions`. That set is memory-only (`:46`) and is drained only by `setActiveVideoPoolReelIDs` (`:77-80`).
  - `:89-123`: reconcile iterates *rows*, so a file with no row is invisible.
  - The LRU (`:239-242`) and "Free Local Storage" (`:553-555`) only touch `.cached` rows.
- **Trigger A (re-save race):** on the playing reel, tap Save, then Saved (unsave), then Save again.
  - The unsave defers the unlink, because the reel is active.
  - The re-save (`BookmarkController.add`, row inserted as `.evicted`) finds the file present and returns without flipping to `.cached`.
  - When you swipe away, the pending deletion deletes the file.
- **Trigger B (unsave then leave):** unsave the reel that is playing, then background or quit the app. iOS then kills it.
  - `pendingDeletions` is lost, and the row is already deleted (`BookmarkController.swift:28-38`).
  - The file stays in `Bookmarks/` with no row. It is never counted, pinned, evicted or freed.
  - If you re-bookmark later, Trigger A's path leaves it counted in the gauge, stuck `.evicted` and unfreeable.
- **Impact:**
  - Offline copies of bookmarks vanish silently.
  - The gauge can creep up with bytes the UI can't free, until `ensureSpaceForBookmark` throws "Cannot evict enough storage: active reels are currently bound" when nothing is bound.
  - Orphans also come from the save-rollback paths (`:357`, `:409`) and from a `keepBookmarkOffline` still running when the row is deleted on the main context.
- **Fix:** (a) re-save wins over a deferred unlink and flips the status; (b) reconcile sweeps files that have no row. Fix (b) covers every source of orphans at once.

```diff
--- a/Sources/InstagramDigest/Engine/MediaCacheManager.swift
+++ b/Sources/InstagramDigest/Engine/MediaCacheManager.swift
@@ -309,9 +309,13 @@
         if fm.fileExists(atPath: bookmarkDestURL.path) {
+            // Re-bookmark wins over a deferred unlink queued by an earlier unsave.
+            pendingDeletions.remove(reelID)
             if item.localStatus != .cached {
+                item.localStatus = .cached          // the isolated copy has landed (B3 contract)
                 item.sizeBytes = Self.diskFileSize(atPath: bookmarkDestURL.path) ?? item.sizeBytes
+                item.lastAccessedAt = Date()
                 try? context.save()
                 // Bytes already occupy disk (and reconcile counts them):
                 // re-derive the ledger instead of adding them a second time.
                 await reconcileBookmarkStorageLedger()
             }
             return
         }
@@ -96,2 +100,3 @@
             var computedBytes: Int64 = 0
+            let knownIDs = Set(bookmarks.map { LibraryPathResolver.sanitizeComponent($0.reelID) })
             for bookmark in bookmarks {
@@ -118,2 +123,13 @@
             try context.save()
             self.totalBookmarkBytes = computedBytes
+            // Sweep files with no row (unsave whose deferred unlink died with the
+            // process, rolled-back saves): invisible to ledger, LRU and Free Storage.
+            let active = Set(activeVideoPoolReelIDs.map { LibraryPathResolver.sanitizeComponent($0) })
+            let files = (try? FileManager.default.contentsOfDirectory(
+                at: pathResolver.bookmarksDirectoryURL, includingPropertiesForKeys: nil)) ?? []
+            for url in files where url.pathExtension == "mp4" {
+                let stem = url.deletingPathExtension().lastPathComponent
+                if !knownIDs.contains(stem) && !active.contains(stem) {
+                    try? FileManager.default.removeItem(at: url)
+                }
+            }
```

- **CI verification (unit test):** use an in-memory container and a separate `MediaCacheManager()`, following the pattern in `AuditFixesTests.testSyncRemoteBookmarksDedupesRepeatedIDs`.
  - Test 1: write a file into `bookmarksDirectoryURL` with no row, call `reconcileBookmarkStorageLedger()`, and assert the file is gone.
  - Test 2: with a row plus a file whose status is `.evicted`, call `keepBookmarkOffline` and assert the status is `.cached`.

---

# P1 — Pipeline, server, account safety

## P1-5 · [Pipeline] The JIT purge protects the *local* digest's week, not the week Pages serves, and the digest is saved before the deploy gate can refuse it

- **Location:**
  - `main.py:1973-1975`: `keep_weeks = {live_week}`, where `live_week` comes from `data/top100_digest.json`.
  - `main.py:2089` and `main.py:2100`: `save_digest_batch` runs **before** the `MIN_DEPLOY_ITEMS` gate at `main.py:2118-2127`.
  - `main.py:285-286`: `_verify_digest_r2_keys` treats any HEAD exception as "missing".
- **Trigger (any case where the local digest disagrees with Pages):**
  - (a) A failed deploy (P0-1).
  - (b) Uploads partly fail mid-way, so the list drops below 150 after gate 5C. The new, short digest is saved, then the deploy is refused.
  - (c) Wi-Fi or R2 5xx during the 250 post-upload HEAD calls drops every reel, and `save_digest_batch([])` writes an empty live digest. This bypasses the empty-save guard at `:2070`.
- **Impact:** at the next run, usually the login resume, `live_week` is the unserved week. `purge_previous_weeks_videos` deletes the week Pages and your phone are playing, so every reel you haven't downloaded dies. Separately, `_record_seen_reel_ids` marks reels that never shipped as seen.
- **Fix:**
  - (1) Record the week Pages actually serves, and keep it.
  - (2) Gate before persisting.
  - (3) Treat only a real 404 as missing.

```diff
--- a/site_builder.py
+++ b/site_builder.py
@@ -604,3 +604,9 @@
         if res.returncode == 0:
             logger.info("Successfully deployed to GitHub Pages! Live at https://vkr1729.github.io/Instagram_digest/")
+            # What Pages (and the iOS app) now serve: the JIT purge must never delete it.
+            try:
+                served = json.loads((site_dir / "data.json").read_text(encoding="utf-8")).get("run_date") or ""
+                atomic_io.durable_write_json(config.DATA_DIR / "deployed_week.json", {"run_date": served})
+            except Exception as exc:
+                logger.warning("Could not record deployed week: %s", exc)
             return True
--- a/main.py
+++ b/main.py
@@ -1973,3 +1973,10 @@
         live_week = _persisted_digest_week()
-        keep_weeks = {live_week} if live_week and live_week != week_id else set()
+        # The local digest can be ahead of Pages (failed/refused deploy): keep both.
+        try:
+            served_week = json.loads((config.DATA_DIR / "deployed_week.json")
+                                     .read_text(encoding="utf-8")).get("run_date") or ""
+        except Exception:
+            served_week = ""
+        keep_weeks = {w for w in (live_week, served_week) if isinstance(w, str) and w and w != week_id}
         live_digest_unreadable = config.DIGEST_BATCH_FILE.exists() and not live_week
@@ -283,4 +290,6 @@
             s3.head_object(Bucket=config.R2_BUCKET_NAME, Key=key)
             return None
-        except Exception:
-            return rid
+        except Exception as exc:
+            # Only a definite 404 proves absence; throttling/5xx/DNS must not drop reels.
+            code = str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", ""))
+            return rid if code in ("404", "NoSuchKey", "NotFound") else None
@@ -2088,3 +2097,2 @@
         extra_manifest = {"budget_capped": True} if budget_capped else None
-        ranker.save_digest_batch(ranked_reels, run_date=week_id, extra_manifest=extra_manifest)
-
@@ -2099,2 +2107,1 @@
             ranked_reels = [r for r in ranked_reels if str(r.get("id")) not in gone]
-            ranker.save_digest_batch(ranked_reels, run_date=week_id, extra_manifest=extra_manifest)
@@ -2111,1 +2118,14 @@
+        # Gate BEFORE persisting: a saved-then-refused digest makes the next
+        # JIT purge treat an unserved week as live. The last "publishing"
+        # checkpoint still holds the full list for the resume.
+        if deploy and len(ranked_reels) < MIN_DEPLOY_ITEMS:
+            logger.error(
+                "Only %d playable reels (minimum %d required); refusing to deploy over previous digest.",
+                len(ranked_reels), MIN_DEPLOY_ITEMS)
+            _alert_sync_abort("deploy refused",
+                              f"only {len(ranked_reels)} playable reels (minimum {MIN_DEPLOY_ITEMS})")
+            return 2
+        ranker.save_digest_batch(ranked_reels, run_date=week_id, extra_manifest=extra_manifest)
+
         # B20: ledger records what SHIPPED — after the verify drop above.
@@ -2118,10 +2138,0 @@  (old gate, now above the save)
-        if deploy and len(ranked_reels) < MIN_DEPLOY_ITEMS:
-            logger.error(
-                "Only %d playable reels (minimum %d required); refusing to deploy over previous digest.",
-                len(ranked_reels), MIN_DEPLOY_ITEMS
-            )
-            _alert_sync_abort(
-                "deploy refused",
-                f"only {len(ranked_reels)} playable reels (minimum {MIN_DEPLOY_ITEMS})",
-            )
-            return 2
```

## P1-6 · [Pipeline] Resume paths retire banked work of the other run kind, then silently start a fresh full scrape and deploy that skips the session probe and follow cooldown

- **Location:**
  - `main.py:237`: `(kind is None or loaded_sync.get("kind") in (None, kind))` applies even when `resume=True`.
  - `main.py:934-939`: on a mismatch the checkpoint is retired.
  - `main.py:940-946`: nothing returns early when `resume=True` adopted nothing.
  - `main.py:822`: resumes skip the step-0 probe.
  - `main.py:2798`: resumes skip the follow cooldown.
- **Callers:**
  - `resume_pending.sh:125` (`--sync --resume` becomes `kind="weekly"`, `main.py:2815`).
  - The dashboard Resume button (`local_server.py:246-251`, `kind="weekly"`).
  - The cookie-alert **Retrigger** page, which POSTs `/api/sync-adhoc` and so calls `run_full_sync(kind="ad-hoc")` (`local_server.py:160-166`).
- **Trigger 1:** the weekly run dies from cookie expiry or a challenge and banks its work. You fix the session and click Retrigger, as the alert email says. The ad-hoc run retires the weekly checkpoint and scrapes everything again.
- **Trigger 2:** any parked ad-hoc checkpoint, or any checkpoint more than 3 days old (`MAX_SYNC_RESUME_AGE_DAYS`). At the next login, `resume_pending.sh` retires it, then carries on into a **full 7-day scrape and deploy in the daytime**. Because `resume=True`, that run has no probe and no cooldown. It then records a "weekly" anchor, which shrinks Friday's window.
- **Impact:** hours of banked scraping are thrown away, and Instagram traffic doubles right after a cookie death or challenge (account risk). You also get an unplanned mid-week deploy.
- **Fix:**

```diff
--- a/main.py
+++ b/main.py
@@ -235,3 +235,4 @@
-        # Checkpoints from a different run kind are a different operation
-        # (ad-hoc delta vs full weekly window) — never adopt them.
-        and (kind is None or loaded_sync.get("kind") in (None, kind))
+        # A FRESH run never adopts another kind's checkpoint (P1-6); a resume
+        # finishes whatever kind is banked.
+        and (resume or kind is None or loaded_sync.get("kind") in (None, kind))
@@ -919,1 +920,2 @@
                 sync_progress = loaded_sync
+                kind = loaded_sync.get("kind") or kind   # a resumed weekly stays "weekly" (anchor)
@@ -946,1 +948,6 @@
                     pass
+    if resume and not dry_run and sync_progress is None:
+        # --resume finishes banked work; it must never become an unplanned
+        # full scrape (it already skipped the session probe and follow cooldown).
+        logger.warning("--resume: no usable banked sync work; not starting a fresh sync.")
+        return 0
--- a/local_server.py
+++ b/local_server.py
@@ -160,7 +160,9 @@
+            # "Retrigger" after an abort must finish the parked run, not retire it.
+            parked = any(config.DATA_DIR.glob("sync_progress_*.json"))
             ret = main_module.run_full_sync(
                 dry_run=False,
                 deploy=deploy,
                 days_back=days_back,
                 since_timestamp=since_ts,
                 kind="ad-hoc",
+                resume=parked,
             )
```

- **Tests to add:** no existing test covers the kind gate. `tests/test_frontier_fixes.py:57-68` calls `_sync_progress_usable` without `kind`. Add two asserts:
  - `_sync_progress_usable(adhoc_ckpt, 15, ts, False, 1, kind="weekly") is False` (the original P1-6 direction is kept).
  - `_sync_progress_usable(adhoc_ckpt, 15, ts, True, 1, kind="weekly") is True` (a resume finishes it).

## P1-7 · [Pipeline] R2 quota math assumes one week, but two are always stored; resumes also count already-uploaded bytes twice, so the week aborts *after* the full scrape

- **Location:**
  - `storage_r2.py:309-312` claims "The kept week is reclaimed by the post-publish rolling purges". It isn't:
    - `purge_expired_r2_objects(max_age_days=RETENTION_DAYS)` uses 8 days (`config.py:111`), so last week's roughly 7-day-old objects survive.
    - `purge_unreferenced_r2_videos` keeps anything referenced by `data/digests/*.json`.
  - The byte budget (`main.py:1918-1935`) and quota comment (`config.py:138`, "5.8 GB weekly feed + 2.0 GB bookmarks") are sized for one week.
  - `main.py:1988`: `check_preflight_quota(estimated_new_bytes=total_batch_bytes)` counts every reel, including those already under `videos/<week>/`.
- **Trigger:**
  - **Heavy weeks:** 3.5 GB kept previous week, plus bookmarks, plus a 4 GB batch.
  - **More commonly:** a `publishing`-stage resume after a partial upload, or a same-day re-run. Example: 3 GB previous week + 1 GB bookmarks + 1.5 GB already uploaded + 3 GB counted as new = 8.5 GB, over 8 GiB, when the real peak is about 7 GB.
- **Impact:** `exit 1 "r2 quota exceeded"` after hours of scraping and downloading. It fails the same way on every resume until the checkpoint ages out. The week is lost.
- **Fix:** count only bytes that are actually new, and trim the lowest-ranked tail to fit instead of discarding the run.

```diff
--- a/main.py
+++ b/main.py
@@ -1988,1 +1988,18 @@
-            if not storage_r2.check_preflight_quota(estimated_new_bytes=total_batch_bytes):
+            # Only bytes not yet under videos/<week>/ are new (resume/re-run double
+            # count). The previous week is always still resident here (RETENTION_DAYS=8
+            # > 7-day cadence), so trim the lowest-ranked tail to fit rather than
+            # discard a finished scrape.
+            present = storage_r2.get_existing_r2_keys(f"videos/{week_id}/")
+            def _new_bytes(reels):
+                return sum(p.stat().st_size for r in reels
+                           if (p := downloaded_paths.get(r["id"])) and p.exists()
+                           and f"videos/{week_id}/{p.name}" not in present)
+            used, _ = storage_r2.get_bucket_storage_usage()
+            headroom = config.R2_STORAGE_QUOTA_BYTES - 200 * 1024 * 1024
+            while used >= 0 and len(ranked_reels) > MIN_DEPLOY_ITEMS \
+                    and used + _new_bytes(ranked_reels) >= headroom:
+                ranked_reels = ranked_reels[:max(MIN_DEPLOY_ITEMS, len(ranked_reels) - 5)]
+                budget_capped = True
+            total_batch_bytes = _new_bytes(ranked_reels)
+            if not storage_r2.check_preflight_quota(estimated_new_bytes=total_batch_bytes):
```

- **Docstring:** also correct `storage_r2.py:309-312` to "the kept week is reclaimed by the next run's JIT purge".
- **Why the key check works:** the key name is the local filename (`main.py:2004-2009`), so `videos/<week>/<p.name>` matches exactly.

## P1-8 · [Pipeline] Session self-heal is broken: `validate()` never starts the browser, so every run re-exports cookies with the wrong key, and dashboard follows always fail

- **Location:**
  - `extractor.py:801-802`: `if not self._page: return False`.
  - Callers pass sessions that were never started: `main.py:719-721` (`_probe_session_once`) and `extractor.py:1957-1964` (`follow_creator`).
  - The refresh at `main.py:670-681` runs the exporter in-process under `.venv`.
  - `cookie_exporter.py:26-27` and `:47-51`: `import dbus` sits *inside* `try/except Exception`. The `ImportError` is swallowed, the code logs "Falling back to default 'peanuts' password", and it returns normally. So `main.py`'s system-python retry (`:678-697`, which only runs on `ImportError`) never runs.
- **Trigger:** every weekly or ad-hoc run (step 0), and every dashboard "add channel" follow.
- **Impact:**
  - **Step 0:** the first `validate()` always fails. It is followed by a wrong-key export and only then a real check. The self-heal can never actually refresh cookies, so the docstring's "stale files self-heal with no human involved" is false. That matters for `--resume` and the mid-run check at `main.py:1246`.
  - **Clobber risk:** each wrong-key export has about a 1/256 chance of passing the padding check and overwriting good cookies with junk (see P2-15).
  - **Follows:** `follow_creator` always returns `session_invalid`, which falsely suggests the cookies are dead.
- **Fix:**

```diff
--- a/extractor.py
+++ b/extractor.py
@@ -801,2 +801,8 @@
         if not self._page:
-            return False
+            try:
+                self.start()   # callers pass fresh sessions (probe, follow_creator)
+            except Exception as exc:
+                logger.warning("Session start failed during validation: %s", exc)
+                return False
+        if not self._page:
+            return False
--- a/cookie_exporter.py
+++ b/cookie_exporter.py
@@ -24,5 +24,5 @@
 def get_chrome_secret_service_password() -> bytes:
     """Retrieve Chrome Safe Storage encryption key from Secret Service via DBus."""
+    import dbus  # ImportError must propagate: main.py retries under /usr/bin/python3
     try:
-        import dbus
         bus = dbus.SessionBus()
```

- **Test to update:** `tests/test_failure_alerts.py:252-253` asserts that `validate()` is False when `_page=None`. With the fix it would launch Chromium, so stub it: `sess.start = lambda: (_ for _ in ()).throw(RuntimeError("no browser"))`.
- **Once follows work:** `local_server.py:357-368` starts one thread per add. A module-level `threading.Lock` around `follow_creator` keeps rapid adds serial.

## P1-9 · [Pipeline] The fail-closed soft-block check is unreachable: the sidebar's "Reels" link satisfies the fallback selector

- **Location:**
  - `extractor.py:690`: `_DISCOVERY_SELECTORS = ("a[href*='/reel/']", "a[href*='/reels/']")`.
  - `extractor.py:693-697`: `_extract_shortcode` only parses `reel/<code>`.
  - `extractor.py:873-894`: the empty-grid check only runs when `anchors` is empty.
- **Trigger:** a logged-in page always has `href="/reels/"` (the sidebar) and `/{handle}/reels/` (the profile tab). When the grid is empty, from a soft block served with HTTP 200 or a grid slower than 4 s, the second selector still returns those links. `_extract_shortcode("/reels/")` returns `""`, so the creator is recorded as having zero reels.
- **Impact:** during a 200-status soft block the run does not stop. It loads every one of about 84 creator pages plus Tier 2, then fails at the viability gate. Carrying on through "we limit how often" is the usual path to a checkpoint.
- **Fix:** only count anchors that actually yield a shortcode.

```diff
--- a/extractor.py
+++ b/extractor.py
@@ -878,4 +878,5 @@
             try:
-                anchors = page.locator(selector).all()
+                anchors = [a for a in page.locator(selector).all()
+                           if _extract_shortcode(a.get_attribute("href") or "")]
             except Exception:
                 anchors = []
```

## P1-10 · [Server] Dashboard and Retrigger syncs bypass the follow cooldown and trust-warming pacing

- **Location:**
  - Both guards exist only in `main.main()`: the cooldown at `main.py:2798-2802` and the pacing doubling at `main.py:2803-2807`.
  - The dashboard workers call `main_module.run_full_sync` directly (`local_server.py:160-166` for ad-hoc, `:246-251` for resume). grep confirms there are no other call sites.
- **Trigger:**
  - `TRUST_WARMING=1`, or a `follow_progress.json` less than 14 days old: the "Run ad-hoc refresh" button and Retrigger scrape at half the intended pacing.
  - A dashboard ad-hoc run right after a mass-follow scrapes immediately. `main.py:753` itself calls that "the highest-risk pattern".
- **Impact:** account risk on exactly the path you use for top-ups.
- **Fix:** make the pacing idempotent and call it from every entry point.

```diff
--- a/main.py
+++ b/main.py
@@ -637,1 +637,8 @@
 ENRICH_PAUSE = (5.0, 1.5, 2.5)  # mu, sigma, floor seconds per item
+_BASE_PAUSES = (CREATOR_PAUSE, ENRICH_PAUSE)
+def _apply_trust_warming_pacing() -> None:
+    """Idempotent: CLI and dashboard entry points both call this."""
+    global CREATOR_PAUSE, ENRICH_PAUSE
+    k = 2 if _trust_warming_active() else 1
+    CREATOR_PAUSE = tuple(v * k for v in _BASE_PAUSES[0])
+    ENRICH_PAUSE = tuple(v * k for v in _BASE_PAUSES[1])
@@ -2803,5 +2810,3 @@
-    if _trust_warming_active():
-        logger.info("Trust warming active: doubling creator/enrich pacing for the young account.")
-        global CREATOR_PAUSE, ENRICH_PAUSE
-        CREATOR_PAUSE = (CREATOR_PAUSE[0] * 2, CREATOR_PAUSE[1] * 2, CREATOR_PAUSE[2] * 2)
-        ENRICH_PAUSE = (ENRICH_PAUSE[0] * 2, ENRICH_PAUSE[1] * 2, ENRICH_PAUSE[2] * 2)
+    _apply_trust_warming_pacing()
--- a/local_server.py
+++ b/local_server.py
@@ -146,1 +146,8 @@
+            if not main_module._check_follow_cooldown():
+                with _SYNC_LOCK:
+                    _SYNC_STATE.update(is_running=False, status="failed",
+                                       last_error="follow cooldown active (recent mass-follow burst)")
+                return
+            main_module._apply_trust_warming_pacing()
             last_run = main_module.get_last_run_info()
@@ (resume worker, before :246)
+            main_module._apply_trust_warming_pacing()   # cooldown exempt for resumes, per main.py policy
```

## P1-11 · [Pipeline] Resuming from an "enriched" checkpoint re-scrapes at least 18 Tier-2 profiles, often right after a challenge

- **Location:**
  - `main.py:1157-1159`: `done_map` comes from `sync_progress.get("done")`. None of the "enriched" payloads write `done` (`main.py:1576-1582`, `:1641-1647`, `:1656-1662`).
  - `main.py:1434-1451`: Tier 2 runs outside the `extraction_complete` block. Its skip test `h in done_map` never matches.
  - `main.py:1457-1458`: the pool-size stop requires `visited_this_run >= 18`.
  - `main.py:1531-1533`: the output is thrown away, because `banked_shortlist` is reused.
- **Trigger:** resuming after a challenge or crash during enrichment or Tier 3, which is the main reason enriched checkpoints exist.
- **Impact:** at least 18 profile loads on an account that was just challenged, for nothing. If Instagram challenges again during them, `main.py:1483-1487` overwrites the checkpoint as `extracting` with no shortlist. The next resume then re-scrapes all of Tier 1.
- **Fix:**

```diff
--- a/main.py
+++ b/main.py
@@ -1433,2 +1433,4 @@
                 # Tier 2: Recommended Creators (up to 8 reels per creator, pinned + unpinned)
-                if recommended_creators:
+                # A banked shortlist already contains Tier 2's output; enriched
+                # checkpoints carry no done map, so never re-scrape on resume.
+                if recommended_creators and banked_shortlist is None:
```

## P1-12 · [Tests] Running `pytest` on the laptop writes fake state into the live `data/`, and your next login then runs an unplanned `--expand --deploy`

- **Evidence (from my own test run):** the suite left these files in the clone's real `data/`. I then deleted them, and the clone is back to clean.
  - `expand_checkpoint_<today>.json`: target 3, with reel `FAIL`.
  - `cookie_attention.json`: "Instagram session expired…".
  - `digests/<today>.json`: 250 fake reels named `banked_0…`.
  - `seen_reel_ids.json`: `reel000…`.
- **Location:**
  - `tests/conftest.py` isolates secrets and email but no state paths.
  - Several paths are frozen when their module is imported, so patching `config.DATA_DIR` later doesn't redirect them:
    - `config.py:18` (`DIGESTS_DIR`)
    - `main.py:381` (`SEEN_IDS_FILE`)
    - `local_server.py:600` (`COOKIE_ATTENTION_FILE`)
  - Examples:
    - `tests/test_durability.py:125-127` patches the batch file and the videos and digests directories but not `DATA_DIR`, so `run_expand` writes its checkpoint to the real `data/` (`main.py:2254`).
    - `tests/test_sync_resume.py:53-63` (`_sync_env`) patches `DATA_DIR` but not `DIGESTS_DIR`.
- **Trigger:** running the suite in the live checkout on the laptop. `python-ci.yml` implies you do this: "Maintainer-hardware suites stay local-only".
- **Impact:**
  - At the next login, `resume_pending.sh:140-153` finds the planted checkpoint and runs `main.py --expand 3 --deploy`. That is an unplanned Instagram session plus a deploy.
  - The dashboard shows a false cookie banner.
  - If the suite runs on the same UTC day as the weekly run, it overwrites that week's real archive in `data/digests/`. That archive is the ground truth for `purge_unreferenced_r2_videos` (`storage_r2.py:214-235`) and for the previous-week page.
- **Fix:** add one autouse fixture that points every state path at `tmp_path`, including the import-time copies. Individual tests that set their own paths still win, because their monkeypatches run afterwards.

```diff
--- a/tests/conftest.py
+++ b/tests/conftest.py
@@ -74,0 +75,17 @@
+
+
+@pytest.fixture(autouse=True)
+def _isolate_state_paths(tmp_path, monkeypatch):
+    """No test may touch the live data/ dir: a planted expand checkpoint makes
+    resume_pending.sh run --expand --deploy at the next login."""
+    import main
+    import local_server
+    data = tmp_path / "state"
+    (data / "digests").mkdir(parents=True)
+    monkeypatch.setattr(config, "DATA_DIR", data)
+    monkeypatch.setattr(config, "DIGESTS_DIR", data / "digests")
+    monkeypatch.setattr(config, "DIGEST_BATCH_FILE", data / "top100_digest.json")
+    monkeypatch.setattr(config, "LAST_RUN_FILE", data / "last_run.json")
+    monkeypatch.setattr(main, "SEEN_IDS_FILE", data / "seen_reel_ids.json")
+    monkeypatch.setattr(local_server, "COOKIE_ATTENTION_FILE", data / "cookie_attention.json")
```

- **Tripwire:** add a session-finish check that fails the run if new `expand_checkpoint_*`, `sync_progress_*` or `digests/*.json` files appeared under the real `ROOT_DIR / "data"`.

---

# P2 — iOS app and app tests

## P2-1 · [App] The local-stall ladder resets on `readyToPlay`, so a truncated file restarts from 0:00 forever and pauses Download All for 10 s each cycle
- **Location:**
  - `AVPlayerPool.swift:580-583` resets `reelStrikes` and `localStallCount` on `.readyToPlay`.
  - The ladder at `:693-708` (first stall: `suspendForWatchdog(10)` plus a forced rebuild from zero).
  - `MediaCacheManager.swift:625` accepts files up to 10% short.
- **Trigger:** a local file that is valid at the front but fails mid-play. Examples are a short or truncated promoted download or disk corruption. `readyToPlay` fires before `FailedToPlayToEndTime`.
- **Impact:** the counter never reaches 2, so eviction and the remote fallback never happen. The reel loops from the start, and Download All stalls in 10 s bursts.
- **Fix:** count failures per visit instead.

```diff
--- a/Sources/InstagramDigest/Engine/AVPlayerPool.swift
+++ b/Sources/InstagramDigest/Engine/AVPlayerPool.swift
@@ -263,1 +263,4 @@
         wantsPlayback = true // navigation is an explicit "play this" gesture
+        // Failure ladder counts per visit: a fresh navigation gets a clean slate.
+        reelStrikes[currentItems[newIndex].id] = nil
+        localStallCount[currentItems[newIndex].id] = nil
@@ -580,7 +583,6 @@
-                if status == .readyToPlay {
-                    // Reset failure strikes on successful load
-                    self.reelStrikes[reel.id] = 0
-                    self.localStallCount[reel.id] = 0
-                } else if status == .failed {
+                // No strike reset on .readyToPlay: truncated/corrupt media reaches
+                // readyToPlay before failing mid-play, which made the ladder loop forever.
+                if status == .failed {
                     self.handlePlaybackError(for: reel, isLocal: isLocal)
                 }
```

## P2-2 · [App] Other downloads also skip the HTTP status check: Download All, thumbnail prefetch, share
- **Location:**
  - `DownloadAllCoordinator.swift:374-400`: `didFinishDownloadingTo` ignores `downloadTask.response`. Only the size tolerance at `:417-422` catches error bodies, and only when the manifest carries `size_bytes`, which `site_builder.py:431-468` injects best-effort.
  - `DownloadAllCoordinator.swift:151`: thumbnails.
  - `InstagramDigestApp.swift:822` and `BookmarksSheet.swift:626`: share temp files.
- **Impact:**
  - A 403 or 404 body becomes a "downloaded" `.mp4` and the batch reports success. Offline, the reel then fails through the local ladder.
  - A 404 thumbnail is cached as `.thumb.jpg` and never re-fetched, because the file exists. `AsyncThumbnailView` prefers local files (`ReelCardView.swift:332-340`), so the tile is black forever.
  - Share sends and caches an error page as `.mp4`.
- **Fix:** add one helper and use it at every site.

```diff
--- a/Sources/InstagramDigest/Models/DigestManifest.swift
+++ b/Sources/InstagramDigest/Models/DigestManifest.swift
@@ -14,0 +15,6 @@
+extension URLResponse {
+    /// URLSession downloads "succeed" on 4xx/5xx and return the error body.
+    var isHTTPSuccess: Bool {
+        ((self as? HTTPURLResponse)?.statusCode).map { (200...299).contains($0) } ?? false
+    }
+}
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@ -378,1 +378,14 @@
     ) {
+        if let resp = downloadTask.response, !resp.isHTTPSuccess {
+            let status = (resp as? HTTPURLResponse)?.statusCode ?? -1
+            try? FileManager.default.removeItem(at: location)
+            Task { @MainActor in
+                guard let entry = self.inFlightTasks.removeValue(forKey: downloadTask.taskIdentifier) else { return }
+                let item = entry.item
+                self.removePersistedResumeData(for: item.id)
+                let retries = (self.retryCounts[item.id] ?? 0) + 1
+                self.retryCounts[item.id] = retries
+                if status >= 500 && retries <= self.maxRetriesPerItem { self.queue.append(item) } else { self.failedInBatch += 1 }
+                self.drainQueue()
+            }
+            return
+        }
@@ -151,1 +164,2 @@
-                let (tmpURL, _) = try await URLSession.shared.download(from: remote)
+                let (tmpURL, resp) = try await URLSession.shared.download(from: remote)
+                guard resp.isHTTPSuccess else { try? FileManager.default.removeItem(at: tmpURL); continue }
```
The share sites at `InstagramDigestApp.swift:822` and `BookmarksSheet.swift:626` get the same guard. There, `throw URLError(.badServerResponse)` falls into the existing link-only fallback.

## P2-3 · [App] Stale resume data makes a reel fail all 4 attempts in every batch for the rest of the week
- **Location:**
  - `DownloadAllCoordinator.swift:333-334` always prefers a persisted `.dat`.
  - `:454-456` replaces it only when the error carries *new* resume data.
  - The IOS-P1-8 change now keeps `.dat` files until promotion (`:424-427`).
- **Trigger:** the app is killed mid-batch, then iOS purges `tmp/` or the device reboots, so the partial file the resume data points to is gone. `downloadTask(withResumeData:)` then fails via `didCompleteWithError` with no fresh resume data.
- **Impact:** every retry reuses the same dead blob, so the reel ends up in `failedInBatch` in every batch until the weekly rollover cleans `ResumeData/`.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@ -454,5 +454,10 @@
-            if let resumeData = (error as NSError).userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
-                self.persistResumeData(resumeData, for: item.id)
-            }
-
-            let isCancelled = (error as NSError).code == NSURLErrorCancelled
+            let isCancelled = (error as NSError).code == NSURLErrorCancelled
+            if let resumeData = (error as NSError).userInfo[NSURLSessionDownloadTaskResumeData] as? Data {
+                self.persistResumeData(resumeData, for: item.id)
+            } else if !isCancelled {
+                // No fresh resume data => any persisted blob is unusable (tmp purged,
+                // object changed). Drop it so the retry starts clean.
+                self.removePersistedResumeData(for: item.id)
+            }
```

## P2-4 · [App] After one batch finishes, Download All is stuck on "Done" until the app is relaunched
- **Location:**
  - `DownloadAllCoordinator.swift:317-319` sets `.completed`, and nothing resets it.
  - `DownloadAllSheet.swift:133-140` and `:211-223`: `.completed` only offers **Done**.
  - **Start** only exists in `.idle` (`:164-178`), and **Retry** only in `.failed` (`:255-270`).
- **Trigger:** you finish an "unwatched only" batch and later want the rest, or the process stays resident and you reopen the sheet.
- **Impact:** there is no way to start another batch without force-quitting. The coordinator is a singleton.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
+++ b/Sources/InstagramDigest/Engine/DownloadAllCoordinator.swift
@@ -185,1 +185,7 @@
     }
+
+    /// A finished batch must not strand the (singleton) sheet on "Done" forever.
+    public func resetIfFinished() {
+        if case .completed = state, !hasActiveDownloads { state = .idle; overallProgress = 0 }
+    }
--- a/Sources/InstagramDigest/Views/Modals/DownloadAllSheet.swift
+++ b/Sources/InstagramDigest/Views/Modals/DownloadAllSheet.swift
@@ -302,1 +302,2 @@
             .onAppear {
+                coordinator.resetIfFinished()
```
When everything is already on disk, pressing Start again completes instantly (`startDownloadAll` at `:117-121`).

## P2-5 · [App] The bookmark player ignores backgrounding and interruptions, and has no fallback when its local copy is bad
- **Location:**
  - `BookmarksSheet.swift:451-470`: it only has `onAppear`, route-change and `onDisappear` hooks.
  - `:487-501`: play/pause trusts `isPlaying`.
  - `:526-535`: local file first, with no failure observation.
- **Trigger:** lock the phone or switch apps while a bookmark plays, or take a call.
- **Impact:**
  - Audio continues until iOS suspends the app. The pool pauses on background (`AVPlayerPool.swift:828-834`); this second `AVPlayer` doesn't.
  - Afterwards `isPlaying` is still true while the player is paused, so your first tap just "pauses" and you have to tap twice.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift
+++ b/Sources/InstagramDigest/Views/Modals/BookmarksSheet.swift
@@ -467,1 +467,10 @@
         }
+        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didEnterBackgroundNotification)) { _ in
+            // Same discipline as the pool: no audio from a backgrounded overlay,
+            // and keep isPlaying truthful so the next tap resumes.
+            player?.pause()
+            isPlaying = false
+        }
+        .onReceive(NotificationCenter.default.publisher(for: AVAudioSession.interruptionNotification)) { _ in
+            isPlaying = (player?.rate ?? 0) > 0
+        }
```

## P2-6 · [App] After a media-services reset the pool keeps its dead `AVQueuePlayer`s
- **Location:**
  - `AVPlayerPool.swift:24` and `:37`: each `Slot` creates its `player` exactly once.
  - `:765-772`: `rebuildCurrentSlot` only replaces the item.
  - It is called from `AudioSessionCoordinator.swift:146-153`.
- **Impact:** after `mediaServicesWereResetNotification`, Apple says to dispose of and recreate every player. The rebuilt item goes into a dead player, so playback is black or silent until you relaunch. This is rare but unrecoverable in-app.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/Engine/AVPlayerPool.swift
+++ b/Sources/InstagramDigest/Engine/AVPlayerPool.swift
@@ -769,2 +769,8 @@
         cancelAllInFlightTasks()
+        // Players are zombies after a media-services reset: recreate all slots.
+        for slot in slots { slot.teardown(detachingLayer: true) }
+        slotPrev = Slot(index: 0); slotCurrent = Slot(index: 1); slotNext = Slot(index: 2)
+        // FeedCollectionViewController re-binds cell layers on this notification.
+        NotificationCenter.default.post(name: Self.didEnterForegroundNotification, object: self)
         let item = currentItems[currentIndex]
```

## P2-7 · [App] Tapping back to "Top N" after browsing a category restarts at #1
- **Location:**
  - `InstagramDigestApp.swift:288-300`: the category handler always sets `activeIndex = 0`.
  - `AVPlayerPool.swift:252-253`: `setReels(..., startIndex: 0)`.
- **Impact:** your place in the full list is lost, and #1 is usually a reel you've already watched. The existing `suppressNextResumeSave` protects the saved position but never *restores* it.
- **Fix:**

```diff
--- a/Sources/InstagramDigest/InstagramDigestApp.swift
+++ b/Sources/InstagramDigest/InstagramDigestApp.swift
@@ -293,8 +293,15 @@
                             if pool.filterByCategory(newCat, allReels: allReels, weekID: m.weekId) {
                                 selectedCategoryId = newCat
-                                suppressNextResumeSave = true
-                                activeIndex = 0
+                                if newCat == "all" {
+                                    // Back to the full list: resume where you left off, not at #1.
+                                    let saved = UserDefaults.standard.string(forKey: "lastActiveReelID_\(m.weekId)")
+                                    let idx = allReels.firstIndex { $0.id == saved } ?? 0
+                                    activeIndex = idx
+                                    pool.setCurrentIndex(idx)
+                                } else {
+                                    suppressNextResumeSave = true
+                                    activeIndex = 0
+                                }
                             }
```

## P2-8 · [Tests] Hosted unit tests share singletons with a live feed that fetches the network, and the test URLs 404, so the failure ladder moves the pool mid-test
- **Location:**
  - `project.yml:44-57`: the unit-test bundle depends on the app, so XcodeGen hosts it in the app.
  - `InstagramDigestApp.swift:77-83` and `:405-407`: the host app mounts `FeedMainView`, fetches the real manifest and calls `setReels` on `AVPlayerPool.shared`.
  - `EngineTests.swift:212-240` and `:305-327`: tests drive the same singleton with `.../videos/2026-09-14/01_test_r0.mp4` URLs, which never existed. They fail, strike twice, and `AVPlayerPool.swift:755-758` calls `setCurrentIndex(+1)` by itself.
- **Impact:** slot-rotation assertions depend on timing. This fits the recent churn of test fixes (`30255d7` "wait for slotCurrent content instead of asserting slot identity"). CI also makes live network calls, including `fetchRemoteBookmarks`, during unit tests.
- **Fix:** don't mount the feed when hosting unit tests.

```diff
--- a/Sources/InstagramDigest/InstagramDigestApp.swift
+++ b/Sources/InstagramDigest/InstagramDigestApp.swift
@@ -77,7 +77,14 @@
     var body: some Scene {
         WindowGroup {
-            FeedMainView()
-                .modelContainer(modelContainer)
-                .preferredColorScheme(.dark)
+            if ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil,
+               !ProcessInfo.processInfo.arguments.contains("-ui-testing") {
+                // Hosting unit tests: no feed, no network, no writes to the singletons under test.
+                Color.black
+            } else {
+                FeedMainView()
+                    .modelContainer(modelContainer)
+                    .preferredColorScheme(.dark)
+            }
         }
     }
```
A deterministic local clip for the rotation tests is covered in RECOMMENDATIONS §1.

## P2-9 · [Tests] XCUITests leak state through UserDefaults, and the fixture's media is (very likely) purged, so the suite can't see playback
- **Location:**
  - `InstagramDigestApp.swift:13-18`: `-ui-testing` wipes the media cache and uses an in-memory store, but leaves UserDefaults alone.
  - The resume position is read from UserDefaults (`:503-507`) and written on every jump (`:597-600`).
  - The fixture is `Resources/data.json` (`run_date` `2026-09-14`), with R2 video URLs.
- **Impact:**
  - `testGridNavigationToReel` (`InstagramDigestUITests.swift:94-121`) asserts that the rank *changes* after tapping item 1. It only passes because CI simulators start fresh. Any rerun on the same simulator, a reordering or `-retry-tests-on-failure` launches at index 1 and fails.
  - With the fixture URLs purged (Assumption 3), every reel fails playback in CI and the pool skip-cascades in the background. No test asserts playback, so a completely dead player would pass UAT. P1-2 would also make the pager follow that cascade.
- **Fix:** reset defaults under `-ui-testing`. For the fixture, use the local-clip approach in RECOMMENDATIONS §1.

```diff
--- a/Sources/InstagramDigest/InstagramDigestApp.swift
+++ b/Sources/InstagramDigest/InstagramDigestApp.swift
@@ -15,3 +15,6 @@
         if isUITesting {
+            if let bid = Bundle.main.bundleIdentifier {
+                UserDefaults.standard.removePersistentDomain(forName: bid)  // resume index, watch timer
+            }
             try? FileManager.default.removeItem(at: LibraryPathResolver.shared.mediaCacheBaseURL)
```

---

# P2 — Pipeline, Worker, server, shell

## P2-10 · [Worker] The 2 GB bookmark byte cap is never enforced below 300 items
- **Location:** `cloudflare/worker.js:94-95`: `over = (c - MAX_ITEMS) + (s > MAX_BYTES ? 1 : 0); if (over <= 0) break;`.
- **Impact:** at 150 bookmarks and 2.5 GB, `over = -149`, so nothing is evicted. Bookmarks grow toward 300 × 50 MB, eat into P1-7's quota and eventually make the weekly pre-flight fail every week.
- **Fix:**

```diff
-    const over = (countRow.c - MAX_ITEMS) + (countRow.s > MAX_BYTES ? 1 : 0);
-    if (over <= 0) break;
+    if (countRow.c <= MAX_ITEMS && countRow.s <= MAX_BYTES) break;
```

## P2-11 · [Worker] Bookmarks over 50 MB are rejected because Telegram's limit is applied to the R2 copy
- **Location:** `worker.js:12` (`MAX_VIDEO_BYTES = 50 MB`, "Telegram Bot API multipart ceiling") and `:292` (`413 TOO_LARGE`). The app ignores non-2xx replies (`DigestDataService.swift:164-167`; the result is discarded at `BookmarkController.swift:85`).
- **Impact:** a long reel's bookmark never gets a durable `bookmarks/` copy. It dies once R2 purges its week, unless the phone still holds an offline copy.
- **Fix:** `if (size > 200 * 1024 * 1024) return json(..., 413);` at `:292`. Keep the existing `<= MAX_VIDEO_BYTES` checks inside the Telegram step (`:216`, `:230`).

## P2-12 · [Pipeline] Post-publish purges can crash the run between saving the digest and deploying
- **Location:** `storage_r2.py:20` imports only `ClientError`, and `:74`, `:189`, `:290` and `:356` catch only that. `main.py:2139-2142` calls the purges unguarded, after the checkpoint files are deleted (`:2133-2137`) and before build and deploy (`:2161-2168`).
- **Trigger:** a network loss that outlasts botocore's retries. `EndpointConnectionError` and `ReadTimeoutError` are `BotoCoreError`, not `ClientError`.
- **Impact:** a traceback and exit 1. There is no site build, no deploy and no checkpoint to resume from.
- **Fix:** `from botocore.exceptions import BotoCoreError, ClientError` and `except (ClientError, BotoCoreError)` at those four sites. Also wrap `main.py:2140-2142` in `try: ... except Exception as exc: logger.warning("post-publish purge skipped: %s", exc)`.

## P2-13 · [Pipeline] Soft-block and challenge pages are classified as rate limits, causing about 140 minutes of sleep and 3 retries on a gated account
- **Location:**
  - `extractor.py:891-892` always raises a plain `InstagramBlocked("...soft-block markers...")`.
  - `main.py:1281-1303` treats any non-login `InstagramBlocked` as a rate limit (`RATE_LIMIT_WAITS_MIN = (20, 40, 80)`).
  - `_CHALLENGE_MARKERS` (`extractor.py:631-642`) leaves out `verify_contactpoint` and `/accounts/confirm`.
- **Fix:** classify inside the empty-grid branch.

```diff
             if _page_html_indicates_block(probe_html):
-                raise InstagramBlocked(f"@{clean_handle}: soft-block markers in empty grid")
+                low = probe_html.lower()
+                if "challenge_required" in low or "suspicious login attempt" in low:
+                    raise InstagramChallenged(f"@{clean_handle}: challenge soft-block in empty grid")
+                if "login_required" in low or "log in to continue" in low:
+                    raise InstagramBlocked(f"@{clean_handle}: login_required soft-block in empty grid")
+                raise InstagramBlocked(f"@{clean_handle}: soft-block markers in empty grid")
```
Also add `"verify_contactpoint"` and `"/accounts/confirm"` to `_CHALLENGE_MARKERS`.

## P2-14 · [Pipeline] A challenge during +100 expand sends no alert, and the checkpoint is retried at every login
- **Location:** `main.py:2389` only catches `CookieExpiredException`. `InstagramChallenged` falls to `except Exception` at `:2414-2429` ("No cookie mail here"), which writes a checkpoint and returns 2. `resume_pending.sh:140-158` then re-runs `--expand` at every login.
- **Fix:** add a branch before `:2414`.

```diff
+            except extractor.InstagramBlocked as exc:
+                _alert_sync_abort("instagram challenge-gated (expand)", str(exc))
+                try:
+                    local_server.raise_cookie_attention(pipeline="expand", reason="Instagram gate during +100 discovery")
+                except Exception as popup_err:
+                    logger.warning("Failed raising cookie attention popup: %s", popup_err)
+                return 2
             except Exception as exc:
```

## P2-15 · [Pipeline] Wrong-key cookie decryption passes the padding check about 1 time in 256 and can overwrite good cookies with junk
- **Location:** `cookie_exporter.py:78-85` checks only the PKCS#7 padding and then decodes with `errors="replace"`. `:144-146` and `:170` use one key for both v10 and v11 cookies.
- **Trigger:** any export with the wrong key. That is every venv run today (P1-8), and a locked keyring on system Python. If the `sessionid` row happens to pass (measured at about 0.37%, so roughly an 18% chance per year of weekly runs), the no-sessionid guard at `:197` is skipped and all three cookie files are rewritten.
- **Fix:** verify the `SHA256(host_key)` prefix that Chrome writes, and decode strictly.

```diff
-def decrypt_chrome_cookie(enc_bytes: bytes, key: bytes, iv: bytes) -> str:
+def decrypt_chrome_cookie(enc_bytes: bytes, key: bytes, iv: bytes, host: str = "") -> str:
@@
     unpadded = padded[:-pad_len]
-    # Linux Chrome prefixes the SHA256(host_key) to the plaintext.
-    return unpadded[32:].decode("utf-8", errors="replace")
+    # Linux Chrome prefixes SHA256(host_key): verifying it rejects wrong-key output.
+    if host and unpadded[:32] != hashlib.sha256(host.encode("utf-8")).digest():
+        return ""
+    try:
+        return unpadded[32:].decode("utf-8")
+    except UnicodeDecodeError:
+        return ""
@@ -170
-            val = decrypt_chrome_cookie(enc_bytes, key, iv)
+            val = decrypt_chrome_cookie(enc_bytes, key, iv, host)
```

## P2-16 · [Pipeline] A total upload outage can't be recovered through either path the code offers
- **Location:**
  - `main.py:2076-2082` writes `shortfall_paused` with `"ranked": []`. On resume, `if banked_ranked:` (`main.py:1023`) is false, so it re-scrapes from scratch.
  - `_run_reconcile` deletes the outbox (`main.py:556-559`) *before* its live-week check refuses (`:567-570`).
- **Fix:**
  - Write the pre-drop list with stage `publishing`: capture `publish_set = list(ranked_reels)` before `:2056` and use `_write_sync_progress("publishing", {"ranked": publish_set, ...})`. The resume then reuses the downloads and retries the uploads.
  - In `_run_reconcile`, move the outbox unlink to after `ranker.save_digest_batch(...)`, and run the `run_date == week_id` check before uploading.

## P2-17 · [Pipeline] The weekly anchor is the run's end time, so reels posted while it runs are never collected
- **Location:** `main.py:2171` calls `save_last_run_info(...)` with no timestamp, so `time.time()` at completion is used (`main.py:167`). The next run cuts off at that point.
- **Impact:** reels posted after their creator was visited but before the run finished (2–6 h on Friday evening, or days for a resumed run) fall on the wrong side of both runs.
- **Fix:** record `run_started_ts = time.time()` near `main.py:809`, persist it in the checkpoint payload (`:951-955`) so resumes keep it, and pass `timestamp=run_started_ts` at `:2171`. The seen-ID ledger already absorbs the overlap.

## P2-18 · [Server] Any website can start an ad-hoc sync and deploy by opening `/retrigger`
- **Location:** `local_server.py:1058` serves `/retrigger` with no checks, and its page auto-POSTs `/api/sync-adhoc` on load (`:1112-1115`). That POST's `Origin` is `127.0.0.1:8080`, so `_is_local_origin` (`:671-688`) accepts it. No HTML route sends `X-Frame-Options`.
- **Trigger:** a page you visit does `window.open('http://127.0.0.1:8080/retrigger')`. This is a targeted attack and unlikely for a personal tool, but it leads to an unplanned scrape using your account.
- **Fix:** only auto-start on same-origin or typed navigations; show a Start button otherwise.

```diff
         if clean_path in ("/retrigger", "/retrigger/"):
+            auto = (self.headers.get("Sec-Fetch-Site") or "").lower() in ("same-origin", "none")
@@ page script
-    fetch('/api/sync-adhoc', { method: 'POST' })
+    function startSync() { fetch('/api/sync-adhoc', { method: 'POST' }) /* ...existing then/catch... */ }
+    if (__AUTO__) startSync(); else document.getElementById('startBtn').style.display = 'inline-block';
@@ response headers
+            self.send_header("X-Frame-Options", "DENY")
```
Replace `__AUTO__` with `"true" if auto else "false"` when rendering, and add a hidden `startBtn`. Send the same header from `/dashboard` and `/channels`.

## P2-19 · [Server] An ad-hoc refresh within 1 hour of the last run becomes a full 7-day scrape
- **Location:** `local_server.py:150-156` and `main.py:2786`: `if 3600 <= elapsed <= 7 * 86400` is the only branch that sets `since_ts`. Otherwise the run uses `days_back=7` with no anchor.
- **Trigger:** clicking "Run ad-hoc refresh" or Retrigger right after Friday's run finishes.
- **Impact:** hours of extra Instagram traffic instead of the promised "quick top-up".
- **Fix:** in the worker, before `:147`, if `time.time() - last_run["timestamp"] < 3600`, set `_SYNC_STATE.update(is_running=False, status="skipped", last_error="last run < 1h ago; nothing new")` and return.

## P2-20 · [Server] A corrupt `sources.json` can still be overwritten (P2-15/16 only partly fixed)
- **Location:**
  - `local_server.py:2042-2048` (blacklist POST): `_load_json_tolerant` quarantines the corrupt file and returns `[]`, so `isinstance(sources, list)` is true and `[]` is written.
  - `:2076-2110` (bulk mute and restore): no corruption guard and no `atomic_io.sources_file_lock()`, and it returns `success: True` even when the write at `:2107` fails.
- **Trigger:** `sources.json` and `data/blacklist.json` are tracked by git and also edited at runtime. A `git pull` or `stash pop` conflict leaves conflict markers in the file.
- **Impact:** an empty channel list, so the weekly run scrapes nothing.
- **Fix:**

```python
def _sources_editable() -> bool:
    src = config.SOURCES_FILE
    if src.exists():
        try: return isinstance(json.loads(src.read_text(encoding="utf-8")), list)
        except Exception: return False
    return not any(src.parent.glob(src.name + ".corrupt-*"))
```

Then:
- Check `_sources_editable()` before any write at `:2042` and `:2082`.
- Wrap `:2081-2105` in `with atomic_io.sources_file_lock():`.
- Return a 500 from the `except` at `:2107`.

## P2-21 · [Server] "Free laptop videos" deletes files a parked sync checkpoint still needs
- **Location:** `local_server.py:887-903` only protects `upload_outbox_*.json` `local_paths`. The `downloaded_paths` of a parked `sync_progress_*.json` are not protected.
- **Impact:** the resume re-downloads up to 250 reels from Instagram, and any that fail are dropped, even if they're already on R2.
- **Fix:** add `*config.DATA_DIR.glob("sync_progress_*.json")` to the loop, and read `(payload or {}).get("local_paths") or (payload or {}).get("downloaded_paths") or {}`.

## P2-22 · [Shell] `install_launcher.sh` has a syntax error (P2-21 regressed)
- **Location:** `install_launcher.sh:16`: `Icon=\"$SCRIPT_DIR/assets/icon.svg\""`. The trailing `"` closes `DESKTOP_ENTRY` early. `bash -n` reports `line 53: unexpected EOF`; under `set -e` the script dies at line 18.
- **Also:** the Desktop Entry spec only honours quotes in `Exec=`. `Path="…"` is passed to GLib as a literal quoted path, so `chdir` fails.
- **Fix:**

```diff
 Exec=\"$SCRIPT_DIR/launch.sh\"
-Path=\"$SCRIPT_DIR\"
-Icon=\"$SCRIPT_DIR/assets/icon.svg\""
+Path=$SCRIPT_DIR
+Icon=$SCRIPT_DIR/assets/icon.svg
```
RECOMMENDATIONS §7 adds a `bash -n` step to CI so this can't regress silently again.

## P2-23 · [Shell] `launch.sh` never replaces a stale server, so after `git pull` dashboard actions keep running old code
- **Location:** `launch.sh:58-69`: every later launch loses `flock -n 9` and just opens the existing server. The server inherits fd 9 for its whole life (`:123-127`), so the build-fingerprint check at `:71-96` can't be reached.
- **Impact:** after you pull this review's fixes, dashboard syncs, which import `main` in-process, still run the old code until the server is killed by hand.
- **Fix:** `exec "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/main.py" --serve 9>&-` at `:127`, and the same at the fallback exec. Before reviving the `pkill`, skip it when `/api/sync-status` or `/api/expand/status` reports `"is_running": true`.

## P2-24 · [Shell] Login auto-resume doesn't wait for the network
- **Location:** `resume_pending.sh:112-126` has no connectivity wait. `run_friday_overnight.sh:32-37` has one and documents why: `network-online.target` does nothing in the user manager.
- **Impact:** after the Friday poweroff, the first login's resume fails the R2 check. It isn't retried until the next login, which can be long enough for the work to age out.
- **Fix:** after `:114`, copy the loop: `for _ in $(seq 1 60); do ping -c1 -W2 -q 1.1.1.1 >/dev/null 2>&1 && break; sleep 5; done`.

## P2-25 · [Shell] The Friday chain powers off while another pipeline holds the lock
- **Location:** `run_friday_overnight.sh:119-126` powers off without checking `data/.pipeline.lock`. The lock might be held by a dashboard expand started after the digest, or a login resume.
- **Fix:** before `:119`:

```bash
if ! flock -n "$APP_DIR/data/.pipeline.lock" true 2>/dev/null; then
    log "Pipeline still running — leaving machine ON."; exit 1
fi
```

## P2-26 · [Pipeline] `InstagramSession.close()` puts every teardown step in one `try`
- **Location:** `extractor.py:776-793`. If `page.close()` raises (for example after a browser crash), `playwright.stop()` is skipped. The driver's asyncio loop stays parked in the thread, and the next `start()` in that thread fails. This is the same failure `170f668` fixed for the probe.
- **Fix:** close page, context, browser and playwright each in its own `try/except Exception: pass`.

## P2-27 · [Pipeline] The session fingerprint changes during a run (user agent re-picked every 40 page loads)
- **Location:**
  - `extractor.py:756`: `user_agent=random.choice(USER_AGENT_POOL)` runs on every context recycle (`RECYCLE_EVERY = 40`), and the pool mixes Windows, macOS and Linux strings.
  - media-info and yt-dlp use a third user agent, `DEFAULT_USER_AGENT` (`:985`, `:1272`, `:1521`).
- **Impact:** about 15 user-agent changes on one `sessionid` per night. That contradicts the file's own invariant that the identity is pinned per authenticated session. Confidence: medium, since it depends on Instagram's heuristics.
- **Fix:** pick it once, the way `_locale` is picked: `if self._user_agent is None: self._user_agent = random.choice(USER_AGENT_POOL)`, then pass `user_agent=self._user_agent`. Ideally pin it to your real Chrome's user agent via `.env`.

## P2-28 · [Pipeline] The yt-dlp download fallback ignores login and rate-limit signals
- **Location:** `extractor.py:1529-1545`. A stderr containing "login required", "rate-limit" or "checkpoint" is logged, retried once, and the loop moves on to the next reel. Feed externals carry `blob:` URLs, so all of them (up to about 100) go through this path with your cookies.
- **Fix:** add a module-level `threading.Event` that is set when stderr contains those tokens. Check it at the top of the function to skip the fallback, and clear it at the start of each run (the dashboard is long-lived). RECOMMENDATIONS §5 generalises this.

## P2-29 · [Server] `/retrigger` can show "Sync Complete!" for a run that never started (B29 only partly fixed)
- **Location:** `local_server.py:131-135` resets `last_error` but not `last_result`. The cookie-refresh abort (`:140-145`) leaves the previous run's `last_result == 0`, and the page treats `data.last_result === 0` as success (`:1139`).
- **Fix:** add `_SYNC_STATE["last_result"] = None` at `:135` (and the resume twin at `:230-234`), and change `:1139` to `showFinished(data.status === 'completed', data.last_error)`.

---

# Optional hardening, accepted at personal scale

- **DNS rebinding (read-only):** `do_HEAD`, `do_GET` and `do_POST` (`local_server.py:979`, `:1035`, `:1685`) never check `Host`. A rebinding page could *read* GET JSON (channels, watch history, lock holder). POSTs stay blocked by the Origin check. One-line fix: return 403 unless `Host`'s hostname is `127.0.0.1`, `localhost` or `[::1]`.
- **Owner key in UserDefaults** (`InstagramDigestApp.swift:416`, `BookmarksSheet.swift:219`): stored in plain text and included in unencrypted device backups. The Keychain is the textbook home, but for one phone and a revocable Worker key this is an acceptable trade.
- **`atomic_io.sources_file_lock`** (`atomic_io.py:70`, `:82`) uses a non-reentrant `threading.Lock` but documents reentrancy. No caller nests today. Switch it to `threading.RLock()` to be safe.
- **Health report outbox count** (`notifier.py:571-581`): counts every `upload_outbox_*.json`, so an old-week outbox keeps the report "unhealthy" forever. Count only the live week's outbox.
- **Worker daily reconcile** (`worker.js:348-357`): can delete a bookmark copied within the same seconds window. Skip objects uploaded less than 15 minutes ago.

# Checked and clean

**App**
- **Ghost-audio and teardown discipline on the main paths:**
  - `wantsPlayback` gates every async play: `AVPlayerPool.swift:489-493`, `:560-562`, `:736-740`.
  - Demoted slots are paused and muted (`:314-315`, `:365-366`), and preloads are muted and paused (`:531-538`).
  - The player pauses on background and re-binds layers on foreground (`:828-844`).
  - Interruption auto-resume is gated on `.active` (`AudioSessionCoordinator.swift:120-123`), and a route change pauses (`:138-143`).
- **Cache and pinning:**
  - Pin-set generation fencing works (`MediaCacheManager.swift:70-81`).
  - The weekly purge pins bookmarked reels and their thumbnails (`:151-199`).
  - `.part` promotion uses an atomic replace with a surfaced failure (`:636-645`).
  - Path components are sanitised (`LibraryPathResolver.swift:32-43`).
  - The manifest cache never stores an empty list (`DigestDataService.swift:53-57`).
- **Decoding:** lossy decoders and duplicate-ID dedupe (`DigestManifest.swift:72-93`).
- **SwiftData model:** flat, with unique keys on every entity and no relationships. The actor uses a local `ModelContext` per call, and no `@Model` crosses an actor boundary. The only cross-context hazard (a row deleted while a copy is in flight) is covered by P1-4's orphan sweep.
- **CI:** no `|| true` or `continue-on-error`. XCUITest gates the Release build and the IPA upload, and there is a 90-minute job timeout.

**Pipeline, server, Worker**
- **`atomic_io` writes:** temp file, fsync, `os.replace` and directory fsync. Credential files are `0600` from the first byte.
- **Pipeline flock:** released in `finally`, with a non-inheritable fd; it covers sync, expand, reconcile, deploy and top-up.
- **R2 purges:** paginated, scoped to `videos/`, never touching `bookmarks/`, with deletes chunked at 1000.
- **Ranker:** deterministic, with no division by zero.
- **Worker API:** every endpoint needs the owner key (constant-time compare), CORS uses an allow-list, sizes and IDs are validated, and the size comes from R2.
- **Local server:** bound to `127.0.0.1` only, blocks path traversal, caps bodies at 4 MB, and escapes every `innerHTML` sink.
- **Checks:** `bash -n` passes for every script except the installer.
