# Technical Architecture: Automated Chrome CDP Mid-Run Cookie Recovery

This document summarizes the end-to-end architecture designed through our `/grill-me` session. It enables the Instagram Digest pipeline to automatically recover from expired cookies mid-run using your live Google Chrome session on Linux via the Chrome DevTools Protocol (CDP), without requiring an external AI to supervise the process.

---

## 1. System Configuration

To allow Python to communicate directly with your running Chrome instance without creating duplicate profiles or interfering with daily browsing:

* **File:** `~/.local/share/applications/google-chrome.desktop` (a user override shadowing `/usr/share/applications/google-chrome.desktop`).
  `~/.config/chrome-flags.conf` does **not** work with the official Google Chrome .deb — verified on this machine: the `/opt/google/chrome/google-chrome` wrapper just `exec`s `$HERE/chrome "$@"` with no flags.conf sourcing (that convention belongs to Arch's chromium package). A flags.conf file would be silently ignored and port 9222 would never open.
* **Configuration:** copy the system entry, then append the flag to every `Exec=` line:
  ```text
  cp /usr/share/applications/google-chrome.desktop ~/.local/share/applications/
  # then edit each Exec= line to:
  Exec=/usr/bin/google-chrome-stable --remote-debugging-port=9222 %U
  ```
* **Effect:** Whenever Google Chrome starts on Linux (via desktop icon, application launcher, or CLI), it automatically opens DevTools port `9222` bound strictly to `localhost` on your `Default` user profile.
* **Single-instance caveat:** flags only apply at process start. If Chrome is already running *without* the flag, launching `google-chrome --remote-debugging-port=9222` just opens a window in the existing process and CDP stays closed. After installing the override, fully quit Chrome once (`chrome://quit` or `pkill chrome`) and relaunch from the launcher.
* **Start-of-run probe:** before scraping, GET `http://localhost:9222/json/version`. On connection refused, fail loudly ("Chrome CDP not reachable — quit Chrome fully and relaunch from the launcher so port 9222 opens") instead of scraping blind into a mid-run abort.
* **Security & Isolation:** The port is only accessible to local processes running on your machine, but it is unauthenticated local browser control by design — any local process can drive your tabs while it is open. Always detach CDP and close the automation tab when done. Your existing bookmarks, extensions, and saved Google Password Manager credentials remain completely intact.

---

## 2. End-to-End Recovery Flow

```mermaid
sequenceDiagram
    autonumber
    participant Pipeline as main.py / extractor.py
    participant Disk as data/sync_progress.json
    participant Recovery as chrome_recovery.py
    participant Chrome as Google Chrome (CDP :9222)
    participant User as You (Desktop / Email)

    Pipeline->>Pipeline: Scrape creators & reels...
    Note over Pipeline: Login-redirect caught! (see §3B triage)
    Pipeline->>Disk: 1. Atomic Checkpoint (all reels & candidates saved, stage=cookie_paused)
    Pipeline->>Recovery: 2. Invoke CDP Recovery Helper once per run (timeout: 30 min)

    Recovery->>Chrome: Probe http://localhost:9222/json/version
    alt CDP closed but Chrome running (stale process, pre-flag launch)
        Recovery->>User: Tell user to fully quit + relaunch Chrome; skip to timeout path
    else Chrome not running at all
        Recovery->>Chrome: Launch google-chrome --remote-debugging-port=9222
    end

    Recovery->>Chrome: Connect via Playwright CDP (http://localhost:9222)
    Recovery->>Chrome: Open/focus Instagram login tab
    Recovery->>Chrome: Attempt auto-submit ONLY on plain login page

    alt Challenge-gated (/challenge/, /checkpoint/, checkpoint_required)
        Note over Recovery: NEVER grind a gated account — abort fast (existing policy)
        Recovery->>Pipeline: Return failure; pipeline exits 2 with banked work
    else Plain login / 2FA prompt
        Recovery->>User: Play desktop chime + notify-send banner
        Recovery->>User: Send urgent email alert
        Note over Recovery,User: Pause pipeline & poll up to 30 min for approval
        User->>Chrome: Enter 2FA code / approve prompt
    end

    Recovery->>Recovery: Validate session (session.validate(), not just sessionid present)
    Recovery->>Pipeline: Re-export cookies (data/cookies.json & cookies.txt)
    Recovery->>Chrome: Close temporary automation tab & detach CDP
    Pipeline->>Pipeline: reload_cookies() + reset_gate() & continue scraping
    Note over Pipeline: Full run completes without repeating scraped work!
```

---

## 3. Core Architectural Components

### A. Zero-Loss Checkpoint Guarantee
Before any recovery attempt is made:
* The pipeline immediately calls `_write_sync_progress("cookie_paused", ...)` in [`main.py`](file:///home/kedarnath-reddy-vallaboina/Instagram_digest/main.py).
* All candidate reels, channel progress, and downloaded video files gathered up to that exact second are safely flushed to `data/sync_progress.json`.
* `cookie_paused` MUST be registered in `RESUMABLE_SYNC_STAGES` (`main.py`) and in `local_server.py`'s `resume_pipeline_state()` / `live_progress_state()` stage lists — otherwise the resume gate retires the checkpoint as unusable and `--resume` exits 0 doing nothing.
* If power is lost or the machine powers down, the run can be resumed later without re-scraping a single channel.

### B. Dedicated Recovery Module (`chrome_recovery.py`)
A lightweight, self-contained Python module that handles:
1. **Probe-first health check:** GET `http://localhost:9222/json/version`. Distinguish "Chrome not running" (safe to launch with the flag) from "Chrome running, CDP closed" (stale pre-flag process — launching again is a no-op; tell the user to fully quit and relaunch).
2. **Auto-launch fallback:** ONLY when no Chrome process exists, start Chrome in the background with `--remote-debugging-port=9222 https://www.instagram.com/`.
3. **CDP Attachment:** Connects using `playwright.chromium.connect_over_cdp("http://localhost:9222")`.
4. **Login triage (plain login vs. challenge-gated):**
   * The common mid-run cookie death in Tier 1/2 creator extraction surfaces as `InstagramBlocked` with `/accounts/login` or `login_required` — NOT `CookieExpiredException` (only the Tier 3 feed path raises that). The hook must catch both.
   * Navigate to `https://www.instagram.com/accounts/login/`. Auto-submit with saved credentials ONLY on a plain login page.
   * On `_CHALLENGE_MARKERS` hits (`/challenge/`, `/checkpoint/`, `checkpoint_required`, `risky_contactpoint`, `/accounts/suspended`): return failure immediately. Grinding a gated account burns hours and risks it — this matches the existing gate philosophy in `ARCHITECTURE.md` §3.
5. **Interactive Wait Loop (plain-login/2FA only):**
   * Plays a system chime (`paplay /usr/share/sounds/freedesktop/stereo/message.oga` or fallback bell).
   * Displays a desktop notification via `notify-send "Instagram Digest" "Instagram session expired. Please approve 2FA in Chrome."`.
   * Sends an email alert with direct instructions.
   * Polls every 3 seconds for up to **30 minutes** for the login challenge to resolve.
6. **Post-export validation:** `sessionid` present in `cookies.json` is NOT proof of a live session. After `export_instagram_cookies()`, run `session.validate()` (or one cheap media-info GET) before resuming — Chrome flushes the Cookies WAL asynchronously, so an immediate export can miss the fresh session. Retry the export/validate a couple of times before declaring success.

### C. In-Memory Session Reload & Scraper Hot-Swap
Once valid cookies are confirmed:
1. Calls [`cookie_exporter.export_instagram_cookies()`](file:///home/kedarnath-reddy-vallaboina/Instagram_digest/cookie_exporter.py) to update `data/cookies.json` and root `cookies.txt`.
2. `InstagramSession` currently injects cookies only in `_open_context()` — add a `reload_cookies()` method that re-injects into the live context (or recycles it), plus call `extractor.reset_gate()` so the account-safety latch from the pre-recovery run doesn't instantly abort the resumed run.
3. Guard with a single-recovery-attempt flag per run: a re-expired session after recovery must abort (exit 2), not loop recovery forever.
4. Resumes scraping the remaining creators on the schedule seamlessly.

### D. Graceful Fallback & Timeout
If 30 minutes elapse without human login or resolution:
* The module cleanly logs the timeout and detaches from Chrome.
* The pipeline exits with return code `2` (the standard checkpoint exit code).
* The progress remains permanently safely parked on disk.
* When you next boot or log into your machine, the existing `resume_pending.sh` auto-resume script picks up the run automatically.

---

## 4. Proposed Code Changes & Impact Map

| File | Change | Purpose |
| :--- | :--- | :--- |
| `~/.local/share/applications/google-chrome.desktop` | [NEW / UPDATE] | Copy from `/usr/share/applications/`, append `--remote-debugging-port=9222` to `Exec=` lines; fully quit + relaunch Chrome once. |
| `chrome_recovery.py` | [NEW] | Probe-first CDP check, plain-login vs. challenge triage, auto-login, desktop alert, wait-loop, post-export validation. Single attempt per run. |
| [`main.py`](file:///home/kedarnath-reddy-vallaboina/Instagram_digest/main.py) | [MODIFY] | Hook BOTH `CookieExpiredException` (feed path) and login-redirect `InstagramBlocked` (Tier 1/2 path) to call `chrome_recovery` before aborting; add `cookie_paused` to `RESUMABLE_SYNC_STAGES`. |
| [`extractor.py`](file:///home/kedarnath-reddy-vallaboina/Instagram_digest/extractor.py) | [MODIFY] | Add `InstagramSession.reload_cookies()`; call `reset_gate()` on the recovery path. |
| [`local_server.py`](file:///home/kedarnath-reddy-vallaboina/Instagram_digest/local_server.py) | [MODIFY] | Accept `cookie_paused` in `resume_pipeline_state()` and `live_progress_state()` so the dashboard shows it as resumable instead of hiding it. |
| `tests/test_chrome_recovery.py` | [NEW] | Unit tests with mocked CDP endpoints: probe outcomes, plain-login vs. challenge triage, 2FA wait loop, post-export validation, timeout, single-attempt guard. |

---

## 5. Known Limits (honest scope)

* **Locked-screen overnight runs still can't self-heal.** Locked GNOME Keyring means `cookie_exporter` keeps last-known-good cookies by design, and `notify-send`/chime needs a `DISPLAY`. For unattended runs the 30-min wait degrades to today's exit-2 + `resume_pending.sh` pickup — no worse, but no better either. This architecture pays off for attended runs.
* **CDP :9222 is unauthenticated local control.** Acceptable on a single-user laptop; never expose it beyond localhost, always detach.
* **`playwright` is a `.venv`-only dependency** (`requirements.txt`); `chrome_recovery.py` must run under `.venv/bin/python`, unlike `cookie_exporter.py` which needs `/usr/bin/python3` for dbus/cryptography.

---

## 6. Comparison: Before vs. After

| Scenario | Current Behavior | New CDP Architecture |
| :--- | :--- | :--- |
| **Cookies expire at 11:30 PM mid-scrape (attended)** | Pipeline immediately aborts with exit code 2, sends email, stops scraping. | Pipeline checkpoints work, opens Chrome, logs in automatically with saved password, and resumes immediately. |
| **Instagram prompts for 2FA / SMS code** | Week is skipped or paused; requires manually opening Chrome, logging in, and running CLI commands. | Chimes your laptop, displays desktop alert, pauses for up to 30 min; resumes the moment you tap approve. |
| **Account is challenge-gated (checkpoint)** | Aborts fast with banked progress (correct). | Same — aborts fast. Auto-login is never attempted on a gated account. |
| **Already scraped 150 reels before error** | Salvaged into checkpoint, but process ends; digest delivery is delayed until manual resume. | 150 reels are preserved in memory; remaining 100 reels are scraped immediately after recovery. |
| **AI token consumption** | 0 tokens (manual restart). | **0 tokens** (100% native local Python & Playwright automation). |
