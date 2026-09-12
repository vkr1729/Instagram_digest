"""
local_server.py — Lightweight local HTTP server with Range-request video streaming and on-demand sync API.
"""

from __future__ import annotations

import json
import logging
import math
import mimetypes
import os
import shutil
import signal
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

import atomic_io
import config
import extractor

logger = logging.getLogger("InstagramDigest.LocalServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_STATE_LOCK = threading.Lock()
_SYNC_LOCK = threading.Lock()
# Mutual exclusion across sync AND expand pipelines: only one digest-mutating
# pipeline may run at a time (they share DIGEST_BATCH_FILE, videos/, and R2).
_PIPELINE_LOCK = threading.Lock()
_SYNC_STATE: dict[str, Any] = {
    "is_running": False,
    "status": "idle",
    "started_at": None,
    "last_result": None,
    "last_error": None,
}


def _pipeline_busy() -> bool:
    """True when either pipeline holds the shared digest-mutating lock."""
    if _PIPELINE_LOCK.locked():
        return True
    with _SYNC_LOCK:
        if _SYNC_STATE["is_running"]:
            return True
    with _EXPAND_LOCK:
        if _EXPAND_STATE["is_running"]:
            return True
    return False


def trigger_adhoc_sync_task(deploy: bool = False) -> dict[str, Any]:
    """Launch an ad-hoc sync thread picking reels between now and the stored last run."""
    import main as main_module

    with _SYNC_LOCK:
        if _SYNC_STATE["is_running"]:
            return {
                "success": True,
                "status": "already_running",
                "message": "Ad-hoc sync is already running in background.",
                "sync_state": dict(_SYNC_STATE),
                "last_run": main_module.get_last_run_info(),
            }

    if not _PIPELINE_LOCK.acquire(blocking=False):
        with _SYNC_LOCK:
            snapshot = dict(_SYNC_STATE)
        return {
            "success": True,
            "status": "already_running",
            "message": "Another pipeline (sync or expand) is already running.",
            "sync_state": snapshot,
            "last_run": main_module.get_last_run_info(),
        }

    with _SYNC_LOCK:
        _SYNC_STATE["is_running"] = True
        _SYNC_STATE["status"] = "running"
        _SYNC_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
        _SYNC_STATE["last_error"] = None

    def _worker():
        try:
            logger.info("Background sync thread started: refreshing Chrome cookies...")
            cookie_exp = config.ROOT_DIR / "cookie_exporter.py"
            if cookie_exp.exists():
                try:
                    import subprocess
                    subprocess.run(["/usr/bin/python3", str(cookie_exp)], capture_output=True, text=True, timeout=25)
                except Exception as c_err:
                    logger.warning("Failed refreshing cookies before sync: %s", c_err)

            last_run = main_module.get_last_run_info()
            since_ts = None
            days_back = 7
            if last_run and "timestamp" in last_run:
                since_ts = int(last_run["timestamp"])
                elapsed = time.time() - last_run["timestamp"]
                days_back = max(1, int(round(elapsed / 86400.0)))
                logger.info("Ad-hoc sync: fetching since %s (~%d days back)",
                            last_run.get("last_run_utc"), days_back)

            ret = main_module.run_full_sync(
                dry_run=False,
                deploy=deploy,
                days_back=days_back,
                since_timestamp=since_ts,
            )
            with _SYNC_LOCK:
                _SYNC_STATE["is_running"] = False
                _SYNC_STATE["status"] = "completed" if ret == 0 else "failed"
                _SYNC_STATE["last_result"] = ret
            if ret == 0:
                # A completed run proves the session works; drop the banner.
                clear_cookie_attention()
        except Exception as exc:
            logger.exception("Ad-hoc sync worker error: %s", exc)
            with _SYNC_LOCK:
                _SYNC_STATE["is_running"] = False
                _SYNC_STATE["status"] = "failed"
                _SYNC_STATE["last_error"] = str(exc)
        finally:
            try:
                _PIPELINE_LOCK.release()
            except RuntimeError:
                pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    return {
        "success": True,
        "status": "started",
        "message": "Ad-hoc sync started in background.",
        "sync_state": dict(_SYNC_STATE),
        "last_run": main_module.get_last_run_info(),
    }


_EXPAND_LOCK = threading.Lock()
_EXPAND_STATE: dict[str, Any] = {
    "is_running": False,
    "status": "idle",
    "started_at": None,
    "last_result": None,
    "last_error": None,
}


def trigger_expand_task(count: int = 100, deploy: bool = True) -> dict[str, Any]:
    """Launch a background thread expanding the active digest by N external reels."""
    import main as main_module

    with _EXPAND_LOCK:
        if _EXPAND_STATE["is_running"]:
            return {
                "success": True,
                "status": "already_running",
                "message": "Digest expansion is already running in background.",
                "state": dict(_EXPAND_STATE),
            }

    if not _PIPELINE_LOCK.acquire(blocking=False):
        with _EXPAND_LOCK:
            snapshot = dict(_EXPAND_STATE)
        return {
            "success": True,
            "status": "already_running",
            "message": "Another pipeline (sync or expand) is already running.",
            "state": snapshot,
        }

    with _EXPAND_LOCK:
        _EXPAND_STATE["is_running"] = True
        _EXPAND_STATE["status"] = "running"
        _EXPAND_STATE["started_at"] = datetime.now(timezone.utc).isoformat()
        _EXPAND_STATE["last_error"] = None

    def _worker():
        try:
            logger.info("Background expand thread started for +%d reels...", count)
            cookie_exp = config.ROOT_DIR / "cookie_exporter.py"
            if cookie_exp.exists():
                try:
                    import subprocess
                    subprocess.run(["/usr/bin/python3", str(cookie_exp)], capture_output=True, text=True, timeout=25)
                except Exception as c_err:
                    logger.warning("Failed refreshing cookies before expand: %s", c_err)

            ret = main_module.run_expand(target_count=count, deploy=deploy)
            with _EXPAND_LOCK:
                _EXPAND_STATE["is_running"] = False
                _EXPAND_STATE["status"] = "completed" if ret == 0 else "failed"
                _EXPAND_STATE["last_result"] = ret
            if ret == 0:
                # A completed run proves the session works; drop the banner.
                clear_cookie_attention()
        except Exception as exc:
            logger.exception("Expand worker error: %s", exc)
            with _EXPAND_LOCK:
                _EXPAND_STATE["is_running"] = False
                _EXPAND_STATE["status"] = "failed"
                _EXPAND_STATE["last_error"] = str(exc)
        finally:
            try:
                _PIPELINE_LOCK.release()
            except RuntimeError:
                pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()

    return {
        "success": True,
        "status": "started",
        "message": f"+{count} Expansion started in background.",
        "state": dict(_EXPAND_STATE),
    }


def refresh_cookies_status() -> dict[str, Any]:
    """Refresh Instagram cookies from Chrome and report freshness signals.

    Runs cookie_exporter.py under /usr/bin/python3 (system interpreter, which
    carries dbus/cryptography — the venv does not), exactly like the expand
    worker and run_weekly.sh, then reads back data/cookies.json. The expand
    and retrigger pipelines already auto-refresh before running; this helper
    backs the manual dashboard button so the owner can confirm a healthy
    login session (sessionid present) before committing to a long +100 run.
    """
    import subprocess

    cookie_exp = config.ROOT_DIR / "cookie_exporter.py"
    if not cookie_exp.exists():
        return {"success": False, "error": "cookie_exporter.py not found."}
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", str(cookie_exp)],
            capture_output=True, text=True, timeout=25,
        )
    except Exception as exc:
        logger.warning("Manual cookie refresh failed: %s", exc)
        return {"success": False, "error": str(exc)}
    if proc.returncode != 0:
        err = ((proc.stderr or "") + (proc.stdout or "")).strip() or "cookie exporter failed"
        logger.warning("Manual cookie refresh failed: %s", err)
        return {"success": False, "error": err[-300:]}
    try:
        cdata = json.loads((config.DATA_DIR / "cookies.json").read_text(encoding="utf-8"))
        cookies = cdata.get("cookies_dict", {})
    except Exception as exc:
        return {"success": False, "error": f"Refresh ran but cookies.json is unreadable: {exc}"}
    if not cookies:
        return {
            "success": False,
            "error": "No Instagram cookies found in Chrome. Log into instagram.com in Chrome and retry.",
        }
    return {
        "success": True,
        "cookie_count": len(cookies),
        "has_sessionid": "sessionid" in cookies,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }


def _schedule_server_shutdown(delay: float = 0.5) -> int:
    """Terminate this server process after `delay` seconds.

    Backs the dashboard kill switch: the HTTP response is flushed first, then
    SIGTERM stops the process so the owner can start fresh via launch.sh.
    Factored as a module-level helper so tests can stub it without killing
    the test runner.
    """
    pid = os.getpid()

    def _kill() -> None:
        logger.warning("Dashboard kill switch engaged; stopping server process %d.", pid)
        os.kill(pid, signal.SIGTERM)

    timer = threading.Timer(delay, _kill)
    timer.daemon = True
    timer.start()
    return pid


_SERVER_BUILD: str | None = None


def server_build() -> str:
    """Short git HEAD of the checkout this server process started from.

    Lets the launcher tell a current server from a stale one holding the
    port (stale servers predate this field entirely). Cached after the first
    call; "unknown" when git is unavailable. Uncommitted changes append
    "-dirty" so the launcher restarts a server running edited code too.
    """
    global _SERVER_BUILD
    if _SERVER_BUILD is None:
        _SERVER_BUILD = "unknown"
        try:
            import subprocess
            proc = subprocess.run(
                ["git", "-C", str(config.ROOT_DIR), "rev-parse", "--short", "HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                _SERVER_BUILD = proc.stdout.strip()
                dirty = subprocess.run(
                    ["git", "-C", str(config.ROOT_DIR), "status", "--porcelain"],
                    capture_output=True, text=True, timeout=5,
                )
                if dirty.returncode == 0 and dirty.stdout.strip():
                    _SERVER_BUILD += "-dirty"
        except Exception:
            pass
    return _SERVER_BUILD


COOKIE_ATTENTION_FILE = config.DATA_DIR / "cookie_attention.json"
_PUBLIC_PORT = 8080
_LIVE_PROGRESS_STALE_SECS = 15 * 60


def _dashboard_url() -> str:
    return f"http://127.0.0.1:{_PUBLIC_PORT}/dashboard"


def raise_cookie_attention(reason: str, pipeline: str) -> bool:
    """Flag a cookie death for the dashboard banner and pop a browser tab.

    Called next to the cookie-alert email sites so an owner at the laptop sees
    it immediately instead of discovering the email later. The popup needs a
    desktop session (DISPLAY/WAYLAND_DISPLAY); headless runs keep the email
    plus the persistent banner. Pops at most once per pending flag so repeated
    runs do not stack tabs. Returns True when a popup was attempted.
    """
    if cookie_attention_state() is not None:
        logger.info("Cookie attention already pending; skipping repeat popup.")
        return False
    try:
        payload = {
            "version": 1, "pipeline": pipeline, "reason": reason,
            "raised_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_write_json(COOKIE_ATTENTION_FILE, payload)
    except Exception as exc:
        logger.warning("Failed writing cookie attention flag: %s", exc)
    if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        logger.info("No desktop session; skipping cookie popup (banner + email remain).")
        return False
    try:
        import subprocess
        proc = subprocess.run(
            ["xdg-open", _dashboard_url()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
        if proc.returncode != 0:
            logger.warning("Cookie attention popup failed (xdg-open exit %d).", proc.returncode)
            return False
        logger.warning("Cookie attention popup opened for %s: %s", pipeline, reason)
        return True
    except Exception as exc:
        logger.warning("Cookie attention popup failed: %s", exc)
        return False


def clear_cookie_attention() -> None:
    """Drop the cookie banner flag after a verified-good refresh."""
    try:
        COOKIE_ATTENTION_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def cookie_attention_state() -> dict[str, Any] | None:
    """Pending cookie banner, if any. Read-only; cleared only by a good refresh.

    Malformed flags (missing pipeline/reason) read as absent so a corrupt
    file can never suppress future popups; the next raise overwrites it.
    """
    try:
        data = json.loads(COOKIE_ATTENTION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("pipeline") and data.get("reason"):
        return data
    return None


def _is_local_origin(origin: str | None, referer: str | None) -> bool:
    """Block CSRF-style cross-origin browser POSTs to mutating endpoints.

    Non-browser clients (curl, tests, local scripts) send no Origin/Referer
    and are always allowed; the server already binds 127.0.0.1 only, so this
    just closes the drive-by-web-page hole. The dashboard's own fetch() sends
    Origin http://127.0.0.1:<port> and passes.
    """
    for value in (origin, referer):
        if not value:
            continue
        try:
            host = urlparse(value).hostname or ""
        except Exception:
            return False
        if host not in ("127.0.0.1", "localhost", "::1"):
            return False
    return True


def _clamp_expand_count(raw: Any) -> int:
    """Sanitize the ?count= parameter: integer clamped to 1..500."""
    try:
        count = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ValueError(f"Invalid expand count: {raw!r}")
    return min(500, max(1, count))


def _file_age_secs(path: Path) -> float | None:
    try:
        return time.time() - path.stat().st_mtime
    except OSError:
        return None


def live_progress_state() -> dict[str, Any]:
    """Active pipeline progress for the dashboard bars (expand + weekly sync).

    File-backed so CLI and background runs report too, not just server
    worker threads. Each entry carries `active`: True while this server runs
    that pipeline or the file is fresh; stale leftovers from crashes report
    active False so the dashboard hides them.
    """
    out: dict[str, Any] = {"expand": None, "sync": None}
    expand_files = sorted(config.DATA_DIR.glob("expand_progress_*.json"))
    if expand_files:
        path = expand_files[-1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            age = _file_age_secs(path)
            with _EXPAND_LOCK:
                running = bool(_EXPAND_STATE["is_running"])
            out["expand"] = {
                "phase": data.get("phase"),
                "done": data.get("done", 0) or 0,
                "total": data.get("total", 0) or 0,
                "target_count": data.get("target_count"),
                "banked": data.get("banked", 0) or 0,
                "active": running or (age is not None and age < _LIVE_PROGRESS_STALE_SECS),
            }
    sync_files = sorted(config.DATA_DIR.glob("sync_progress_*.json"))
    if sync_files:
        path = sync_files[-1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = None
        if isinstance(data, dict):
            age = _file_age_secs(path)
            with _SYNC_LOCK:
                running = bool(_SYNC_STATE["is_running"])
            done_map = data.get("done") or {}
            candidates = data.get("candidates") or []
            enriched = data.get("enriched") or []
            ranked = data.get("ranked") or []
            total = data.get("total_sources") or 0
            published = data.get("published") or 0
            published_total = data.get("published_total") or 0
            active = running or (age is not None and age < _LIVE_PROGRESS_STALE_SECS)
            if (not active and data.get("stage") == "cooling_down"
                    and isinstance(data.get("resumes_in_min"), (int, float))
                    and age is not None):
                # Long backoff sleeps outlast the crash-staleness window but the
                # run is alive: stay visible until the sleep should have ended.
                active = age < data["resumes_in_min"] * 60 + 600
            out["sync"] = {
                "stage": data.get("stage"),
                "visited": len(done_map) if isinstance(done_map, dict) else 0,
                "total": total if isinstance(total, int) and total > 0 else None,
                "candidates": len(candidates) if isinstance(candidates, list) else 0,
                "enriched": len(enriched) if isinstance(enriched, list) else 0,
                "ranked": len(ranked) if isinstance(ranked, list) else 0,
                "published": published if isinstance(published, int) else 0,
                "published_total": published_total if isinstance(published_total, int) else 0,
                "blocked_handle": data.get("blocked_handle"),
                "resumes_in_min": data.get("resumes_in_min"),
                "active": active,
            }
    return out


def resume_pipeline_state() -> dict[str, Any]:
    """Read-only inventory of work parked for resume (checkpoints/progress).

    Backs the dashboard's pending-resume lane. Never mutates anything; only
    the pipelines themselves clear their own checkpoints on completion.
    """
    pending_expand: list[dict[str, Any]] = []
    for ckpt in sorted(config.DATA_DIR.glob("expand_checkpoint_*.json")):
        try:
            data = json.loads(ckpt.read_text(encoding="utf-8"))
        except Exception:
            continue
        reels = data.get("reels") if isinstance(data, dict) else data
        if not isinstance(reels, list):
            continue
        target = data.get("target_count", 100) if isinstance(data, dict) else 100
        try:
            updated = datetime.fromtimestamp(ckpt.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            continue
        pending_expand.append({
            "file": ckpt.name,
            "target_count": target if isinstance(target, int) else 100,
            "banked": len([r for r in reels if isinstance(r, dict) and r.get("id")]),
            "updated_at": updated,
        })
    pending_sync: list[dict[str, Any]] = []
    for prog in sorted(config.DATA_DIR.glob("sync_progress_*.json")):
        try:
            data = json.loads(prog.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("stage") not in ("extracting", "enriched", "ranked"):
            continue
        try:
            updated = datetime.fromtimestamp(prog.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            continue
        entry: dict[str, Any] = {
            "file": prog.name,
            "stage": data["stage"],
            "updated_at": updated,
        }
        if data["stage"] == "ranked":
            entry["banked"] = len([r for r in data.get("ranked", []) if isinstance(r, dict) and r.get("id")])
        elif data["stage"] == "enriched":
            entry["banked"] = len([r for r in data.get("enriched", []) if isinstance(r, dict) and r.get("id")])
        else:
            entry["banked"] = len([
                r for r in data.get("candidates", []) if isinstance(r, dict) and r.get("id")
            ])
            done = data.get("done") or {}
            entry["creators_visited"] = len(done) if isinstance(done, dict) else 0
        pending_sync.append(entry)
    return {"expand": pending_expand, "sync": pending_sync}


def _atomic_write_json(path: Path, data: Any) -> None:
    """Crash-safe JSON write: temp + flush + fsync + atomic replace + dir fsync."""
    atomic_io.durable_write_json(path, data)


def _load_json_tolerant(path: Path, default: Any) -> Any:
    """Load JSON, quarantining corrupt files instead of silently discarding them.

    A corrupt state file previously parsed as empty (silent state wipe).
    Now the corrupt bytes are preserved alongside for forensics.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as exc:
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = path.with_name(f"{path.name}.corrupt-{ts}")
            shutil.copy2(path, backup)
            logger.warning("Quarantined corrupt %s to %s: %s", path, backup, exc)
        except Exception:
            logger.warning("Unreadable %s; starting fresh: %s", path, exc)
        return default


class LocalDigestHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler supporting partial video range streaming and on-demand sync API."""
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path

        if clean_path in ("/", "/index.html"):
            local_index = config.SITE_DIR / "local_index.html"
            if not local_index.exists():
                local_index = config.SITE_DIR / "index.html"
            if local_index.exists():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(local_index.stat().st_size))
                self.end_headers()
                return
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

        if clean_path.startswith("/videos/"):
            video_rel = clean_path.replace("/videos/", "")
            video_path = config.VIDEOS_DIR / video_rel
            if video_path.exists() and video_path.is_file():
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(video_path.stat().st_size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return

        file_path = config.SITE_DIR / clean_path.lstrip("/")
        if file_path.exists() and file_path.is_file():
            content_type = self.guess_type(str(file_path))
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(file_path.stat().st_size))
            self.end_headers()
            return

        self.send_response(HTTPStatus.OK)
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path

        # /api/sync-status -> return current sync status
        if clean_path in ("/api/sync-status", "/api/sync-status/"):
            with _SYNC_LOCK:
                state = dict(_SYNC_STATE)
            state["server_build"] = server_build()
            body = json.dumps(state).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # /retrigger -> launch sync with cookie refresh and render live status page
        if clean_path in ("/retrigger", "/retrigger/"):
            trigger_adhoc_sync_task(deploy=True)
            html_content = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Instagram Digest — Retriggering Sync</title>
  <style>
    body {
      margin: 0; padding: 0; background: #09090b; color: #f4f4f5;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
      display: flex; align-items: center; justify-content: center; min-height: 100vh;
    }
    .card {
      background: #141419; border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 20px;
      padding: 36px 28px; max-width: 440px; width: 90%; text-align: center;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.7);
    }
    .spinner {
      width: 56px; height: 56px; border: 4px solid rgba(255, 255, 255, 0.1);
      border-top: 4px solid #fd1d1d; border-radius: 50%; animation: spin 1s linear infinite;
      margin: 0 auto 20px;
    }
    @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
    h2 {
      margin: 0 0 10px; font-size: 20px; font-weight: 700;
      background: linear-gradient(135deg, #f09433, #e6683c, #dc2743, #cc2366, #bc1888);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
    }
    p { color: #a1a1aa; font-size: 14px; line-height: 1.5; margin: 0 0 20px; }
    .status-badge {
      display: inline-block; background: rgba(255, 255, 255, 0.06); padding: 6px 14px;
      border-radius: 20px; font-size: 12px; font-weight: 600; color: #38bdf8;
    }
    .btn {
      display: inline-block; margin-top: 18px; padding: 10px 22px; border-radius: 12px;
      background: #27272a; color: #fff; text-decoration: none; font-size: 13px; font-weight: 600;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="spinner" id="spinner"></div>
    <h2 id="title">Refreshing Instagram Feed</h2>
    <p id="msg">Decrypting fresh cookies from Chrome and curating the top 250 reels...</p>
    <div class="status-badge" id="badge">Sync Running</div>
    <div><a href="/" class="btn" id="homeBtn" style="display:none;">Return to Viewer</a></div>
  </div>
  <script>
    async function checkStatus() {
      try {
        const res = await fetch('/api/sync-status');
        const data = await res.json();
        if (!data.is_running) {
          if (data.status === 'completed' || data.last_result === 0) {
            document.getElementById('title').textContent = 'Sync Complete!';
            document.getElementById('msg').textContent = 'Your digest has been refreshed. Redirecting to viewer...';
            document.getElementById('badge').textContent = 'Completed';
            document.getElementById('badge').style.color = '#4ade80';
            document.getElementById('spinner').style.display = 'none';
            setTimeout(() => { window.location.href = '/'; }, 1800);
            return;
          } else {
            document.getElementById('title').textContent = 'Sync Finished';
            document.getElementById('msg').textContent = data.last_error || 'Check logs for details.';
            document.getElementById('badge').textContent = 'Done';
            document.getElementById('spinner').style.display = 'none';
            document.getElementById('homeBtn').style.display = 'inline-block';
            return;
          }
        }
      } catch (e) {}
      setTimeout(checkStatus, 2000);
    }
    checkStatus();
  </script>
</body>
</html>""".encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html_content)))
            self.end_headers()
            self.wfile.write(html_content)
            return

        # Root route -> serve local_index.html
        if clean_path in ("/", "/index.html"):
            local_index = config.SITE_DIR / "local_index.html"
            if not local_index.exists():
                local_index = config.SITE_DIR / "index.html"

            if local_index.exists():
                content = local_index.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            else:
                self.send_error(HTTPStatus.NOT_FOUND, "Digest site not yet built. Run 'python main.py --build-only'")
                return

        # Video streaming route -> /videos/{path}
        if clean_path.startswith("/videos/"):
            rel_video_path = clean_path[len("/videos/"):]
            video_file = config.VIDEOS_DIR / rel_video_path
            if video_file.exists() and video_file.is_file():
                self.serve_video_file(video_file)
                return

        # Static assets from site/. API paths never resolve to files: a stray
        # site/api/* file must not shadow a real endpoint (or fake one).
        site_file = config.SITE_DIR / clean_path.lstrip("/")
        if not clean_path.startswith("/api/") and site_file.exists() and site_file.is_file():
            mime, _ = mimetypes.guess_type(str(site_file))
            content = site_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime or "application/octet-stream")
            if site_file.name == "sw.js":
                self.send_header("Service-Worker-Allowed", "/")
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        # API Status route
        if clean_path == "/api/status":
            status = {
                "status": "ready",
                "sources_count": len(extractor.load_sources()),
                "has_batch": config.DIGEST_BATCH_FILE.exists(),
                "retention_days": config.RETENTION_DAYS,
            }
            body = json.dumps(status).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API Watched route -> /api/watched?week_id=...
        if clean_path == "/api/watched":
            query = parse_qs(parsed.query)
            week_id = query.get("week_id", ["default"])[0]
            watched_data = _load_json_tolerant(config.WATCHED_FILE, {})
            watched_list = watched_data.get(week_id, [])
            body = json.dumps({"watched": watched_list}).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API Blacklist route -> /api/blacklist
        if clean_path == "/api/blacklist":
            data = _load_json_tolerant(config.BLACKLIST_FILE, {"creators": []})
            body = json.dumps(data).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API Ad-hoc sync status route -> /api/sync-adhoc
        if clean_path in ("/api/sync-adhoc", "/api/sync-adhoc/status"):
            import main as main_module
            with _SYNC_LOCK:
                state_copy = dict(_SYNC_STATE)
            last_run = main_module.get_last_run_info()
            resp = {
                "success": True,
                "sync_state": state_copy,
                "last_run": last_run,
            }
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API resume-state route -> /api/resume-state (pending checkpoints/progress)
        if clean_path in ("/api/resume-state", "/api/resume-state/"):
            resp = {"success": True, **resume_pipeline_state()}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API Expand status route -> /api/expand/status
        if clean_path in ("/api/expand/status", "/api/expand/status/"):
            with _EXPAND_LOCK:
                state_copy = dict(_EXPAND_STATE)
            resp = {"success": True, "state": state_copy}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API live-progress route -> /api/live-progress (dashboard bars)
        if clean_path in ("/api/live-progress", "/api/live-progress/"):
            resp = {"success": True, **live_progress_state()}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # API cookie-attention route -> /api/cookie-attention (banner flag)
        if clean_path in ("/api/cookie-attention", "/api/cookie-attention/"):
            resp = {"success": True, "attention": cookie_attention_state()}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # Web Route: /channels
        if clean_path in ("/channels", "/channels/"):
            channels_template = config.TEMPLATES_DIR / "channels.html"
            if channels_template.exists():
                content = channels_template.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # Web Route: /dashboard (desktop ops page, raw static template)
        if clean_path in ("/dashboard", "/dashboard/"):
            dashboard_template = config.TEMPLATES_DIR / "dashboard.html"
            if dashboard_template.exists():
                content = dashboard_template.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # Web Route: /viewer (explicit viewer URL; root serves it too)
        if clean_path in ("/viewer", "/viewer/"):
            viewer_file = config.SITE_DIR / "local_index.html"
            if not viewer_file.exists():
                viewer_file = config.SITE_DIR / "index.html"
            if viewer_file.exists():
                content = viewer_file.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        # API Route: /api/channels
        if clean_path == "/api/channels":
            sources = []
            if config.SOURCES_FILE.exists():
                sources = _load_json_tolerant(config.SOURCES_FILE, [])

            blacklist = set()
            if config.BLACKLIST_FILE.exists():
                b_data = _load_json_tolerant(config.BLACKLIST_FILE, {"creators": []})
                blacklist = set(c.lower().replace("@", "") for c in b_data.get("creators", []))

            source_map = {}
            for s in sources:
                h = s.get("handle", "").lower().replace("@", "")
                if h:
                    source_map[h] = {
                        "handle": h,
                        "name": s.get("name", h),
                        "category": s.get("category", "entertainment"),
                        "is_blacklisted": (h in blacklist),
                    }

            for bh in blacklist:
                if bh not in source_map:
                    source_map[bh] = {
                        "handle": bh,
                        "name": bh,
                        "category": "entertainment",
                        "is_blacklisted": True,
                    }

            channel_list = sorted(list(source_map.values()), key=lambda x: x["handle"])
            resp_data = {
                "total": len(channel_list),
                "channels": channel_list,
            }
            body = json.dumps(resp_data, ensure_ascii=False).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "File Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        if not _is_local_origin(self.headers.get("Origin"), self.headers.get("Referer")):
            self.send_error(HTTPStatus.FORBIDDEN, "Cross-origin POST rejected")
            return
        if parsed.path in ("/api/sync-adhoc", "/api/sync-adhoc/"):
            logger.info("Ad-hoc midweek sync triggered via API.")
            # Deploy like the weekly run so midweek reels reach the mobile PWA;
            # run_full_sync still refuses to deploy over a healthy digest when
            # the top-up comes back too small (MIN_DEPLOY_ITEMS gate).
            resp = trigger_adhoc_sync_task(deploy=True)
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path in ("/api/expand", "/api/expand/"):
            query = parse_qs(parsed.query)
            try:
                count = _clamp_expand_count(query.get("count", ["100"])[0])
            except ValueError as exc:
                self.send_error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            logger.info("Digest expansion (+%d) triggered via API.", count)
            resp = trigger_expand_task(count=count, deploy=True)
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path in ("/api/cookies/refresh", "/api/cookies/refresh/"):
            logger.info("Manual cookie refresh triggered via API.")
            resp = refresh_cookies_status()
            if resp.get("success") and resp.get("has_sessionid"):
                clear_cookie_attention()
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/sync-following":
            logger.info("On-demand following sync triggered via dashboard API.")
            try:
                sources = extractor.sync_following_accounts(force=True)
                resp = {"success": True, "sources_count": len(sources)}
            except Exception as e:
                resp = {"success": False, "error": str(e)}

            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/watched":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            try:
                payload = json.loads(post_body.decode("utf-8"))
            except Exception:
                payload = {}

            week_id = payload.get("week_id", "default")
            action = payload.get("action", "add")
            reel_id = payload.get("reel_id")

            with _STATE_LOCK:
                watched_data = _load_json_tolerant(config.WATCHED_FILE, {})

                if action == "reset":
                    watched_data[week_id] = []
                elif reel_id:
                    current_list = list(dict.fromkeys(watched_data.get(week_id, []) + [reel_id]))
                    watched_data[week_id] = current_list

                try:
                    _atomic_write_json(config.WATCHED_FILE, watched_data)
                except Exception as e:
                    logger.error("Failed writing watched.json: %s", e)

            resp = {"success": True, "watched": watched_data.get(week_id, [])}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/watched/bulk":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            try:
                payload = json.loads(post_body.decode("utf-8"))
            except Exception:
                payload = {}

            week_id = payload.get("week_id", "default")
            watched_ids = payload.get("watched_ids", [])

            with _STATE_LOCK:
                watched_data = _load_json_tolerant(config.WATCHED_FILE, {})

                current_set = set(watched_data.get(week_id, []))
                for wid in watched_ids:
                    if wid:
                        current_set.add(wid)

                watched_data[week_id] = list(current_set)
                try:
                    _atomic_write_json(config.WATCHED_FILE, watched_data)
                except Exception as e:
                    logger.error("Failed bulk writing watched.json: %s", e)

            resp = {"success": True, "watched": watched_data.get(week_id, [])}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/blacklist":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            try:
                payload = json.loads(post_body.decode("utf-8"))
            except Exception:
                payload = {}

            handle = payload.get("creator_handle", "").strip().lower().replace("@", "")
            action = payload.get("action", "add")

            with _STATE_LOCK:
                data = _load_json_tolerant(config.BLACKLIST_FILE, {"creators": []})

                creators = set(c.lower().replace("@", "") for c in data.get("creators", []))
                if action == "remove":
                    creators.discard(handle)
                elif handle:
                    creators.add(handle)

                data["creators"] = sorted(list(creators))
                try:
                    _atomic_write_json(config.BLACKLIST_FILE, data)
                    logger.info("Updated blacklist.json with %d creators.", len(data["creators"]))
                except Exception as e:
                    logger.error("Failed saving blacklist.json: %s", e)

                # Also remove creator from sources.json so it never syncs or extracts again
                if handle and action == "add" and config.SOURCES_FILE.exists():
                    try:
                        sources = _load_json_tolerant(config.SOURCES_FILE, [])
                        new_sources = [s for s in sources if s.get("handle", "").lower().replace("@", "") != handle]
                        _atomic_write_json(config.SOURCES_FILE, new_sources)
                        logger.info("Removed @%s from sources.json permanently.", handle)
                    except Exception as e:
                        logger.warning("Could not prune sources.json: %s", e)

            resp = {"success": True, "blacklisted": data["creators"]}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/channels/bulk-unselect":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len) if content_len > 0 else b"{}"
            try:
                payload = json.loads(post_body.decode("utf-8"))
            except Exception:
                payload = {}

            handles_in = payload.get("creator_handles", [])
            action = payload.get("action", "add")  # "add" to mute, "remove" to restore
            clean_handles = set(h.lower().replace("@", "").strip() for h in handles_in if h)

            with _STATE_LOCK:
                b_data = _load_json_tolerant(config.BLACKLIST_FILE, {"creators": []})

                blacklist = set(c.lower().replace("@", "") for c in b_data.get("creators", []))

                sources = []
                if config.SOURCES_FILE.exists():
                    sources = _load_json_tolerant(config.SOURCES_FILE, [])

                if action == "add":
                    blacklist.update(clean_handles)
                    # Prune from sources.json
                    sources = [s for s in sources if s.get("handle", "").lower().replace("@", "") not in clean_handles]
                elif action == "remove":
                    blacklist.difference_update(clean_handles)
                    # Restore to sources.json if missing
                    existing_handles = set(s.get("handle", "").lower().replace("@", "") for s in sources)
                    for h in clean_handles:
                        if h not in existing_handles:
                            sources.append({
                                "handle": h,
                                "name": h,
                                "category": "entertainment",
                                "enabled": True
                            })

                b_data["creators"] = sorted(list(blacklist))
                try:
                    _atomic_write_json(config.BLACKLIST_FILE, b_data)
                    _atomic_write_json(config.SOURCES_FILE, sources)
                    logger.info("Bulk updated channels: %d muted total, %d active sources.", len(b_data["creators"]), len(sources))
                except Exception as e:
                    logger.error("Error writing bulk channel updates: %s", e)

            resp = {
                "success": True,
                "action": action,
                "modified_count": len(clean_handles),
                "total_blacklisted": len(b_data["creators"]),
                "total_sources": len(sources),
            }
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path in ("/api/server/shutdown", "/api/server/shutdown/"):
            logger.warning("Dashboard kill switch triggered via API.")
            pid = _schedule_server_shutdown()
            resp = {"success": True, "pid": pid,
                    "message": f"Server process {pid} stopping; relaunch via launch.sh."}
            body = json.dumps(resp).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "Unknown API Route")

    def serve_video_file(self, video_path: Path):
        """Serve video supporting HTTP 206 Partial Content Range requests for video seeking."""
        file_size = video_path.stat().st_size
        range_header = self.headers.get("Range")

        if not range_header:
            # Full file
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Access-Control-Allow-Origin", "*")
            try:
                with video_path.open("rb") as f:
                    shutil.copyfileobj(f, self.wfile)
            except (ConnectionResetError, BrokenPipeError):
                pass
            return

        # Partial range request (e.g. bytes=0-1024)
        bytes_prefix = "bytes="
        if not range_header.startswith(bytes_prefix):
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return

        range_str = range_header[len(bytes_prefix):].strip()
        parts = range_str.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1

        if start >= file_size or end >= file_size or start > end:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.end_headers()
            return

        chunk_size = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(chunk_size))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            with video_path.open("rb") as f:
                f.seek(start)
                remaining = chunk_size
                buf_size = 256 * 1024  # 256 KB fast buffer
                while remaining > 0:
                    read_bytes = f.read(min(remaining, buf_size))
                    if not read_bytes:
                        break
                    self.wfile.write(read_bytes)
                    remaining -= len(read_bytes)
        except (ConnectionResetError, BrokenPipeError):
            pass


def run_local_server(port: int = 8080) -> None:
    """Run multi-threaded local dashboard server on specified port."""
    global _PUBLIC_PORT
    _PUBLIC_PORT = port
    server_address = ("127.0.0.1", port)
    httpd = ThreadingHTTPServer(server_address, LocalDigestHandler)
    logger.info("Instagram Digest multi-threaded server running at http://127.0.0.1:%d/", port)
    print(f"\n=======================================================")
    print(f" Instagram Digest Multi-Threaded Local Dashboard Ready!")
    print(f" URL: http://127.0.0.1:{port}/")
    print(f" Press Ctrl+C to stop.")
    print(f"=======================================================\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping local server...")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Instagram Digest Local Server")
    parser.add_argument("--port", type=int, default=8080, help="Port to bind (default: 8080)")
    args = parser.parse_args()
    run_local_server(port=args.port)
