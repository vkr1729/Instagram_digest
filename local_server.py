"""
local_server.py — Lightweight local HTTP server with Range-request video streaming and on-demand sync API.
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import shutil
from http import HTTPStatus
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import config
import extractor

logger = logging.getLogger("InstagramDigest.LocalServer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class LocalDigestHandler(SimpleHTTPRequestHandler):
    """Custom HTTP handler supporting partial video range streaming and on-demand sync API."""
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        parsed = urlparse(self.path)
        clean_path = parsed.path

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

        # Static assets from site/
        site_file = config.SITE_DIR / clean_path.lstrip("/")
        if site_file.exists() and site_file.is_file():
            mime, _ = mimetypes.guess_type(str(site_file))
            content = site_file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime or "application/octet-stream")
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
            watched_data = {}
            if config.WATCHED_FILE.exists():
                try:
                    watched_data = json.loads(config.WATCHED_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass
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
            data = {"creators": []}
            if config.BLACKLIST_FILE.exists():
                try:
                    data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass
            body = json.dumps(data).encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
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

        # API Route: /api/channels
        if clean_path == "/api/channels":
            sources = []
            if config.SOURCES_FILE.exists():
                try:
                    sources = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass

            blacklist = set()
            if config.BLACKLIST_FILE.exists():
                try:
                    b_data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
                    blacklist = set(c.lower().replace("@", "") for c in b_data.get("creators", []))
                except Exception:
                    pass

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

            watched_data = {}
            if config.WATCHED_FILE.exists():
                try:
                    watched_data = json.loads(config.WATCHED_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass

            if action == "reset":
                watched_data[week_id] = []
            elif reel_id:
                current_list = list(dict.fromkeys(watched_data.get(week_id, []) + [reel_id]))
                watched_data[week_id] = current_list

            try:
                config.WATCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
                config.WATCHED_FILE.write_text(json.dumps(watched_data, indent=2), encoding="utf-8")
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

            watched_data = {}
            if config.WATCHED_FILE.exists():
                try:
                    watched_data = json.loads(config.WATCHED_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass

            current_set = set(watched_data.get(week_id, []))
            for wid in watched_ids:
                if wid:
                    current_set.add(wid)

            watched_data[week_id] = list(current_set)
            try:
                config.WATCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
                config.WATCHED_FILE.write_text(json.dumps(watched_data, indent=2), encoding="utf-8")
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

            data = {"creators": []}
            if config.BLACKLIST_FILE.exists():
                try:
                    data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass

            creators = set(c.lower().replace("@", "") for c in data.get("creators", []))
            if action == "remove":
                creators.discard(handle)
            elif handle:
                creators.add(handle)

            data["creators"] = sorted(list(creators))
            try:
                config.BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
                config.BLACKLIST_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
                logger.info("Updated blacklist.json with %d creators.", len(data["creators"]))
            except Exception as e:
                logger.error("Failed saving blacklist.json: %s", e)

            # Also remove creator from sources.json so it never syncs or extracts again
            if handle and action == "add" and config.SOURCES_FILE.exists():
                try:
                    sources = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
                    new_sources = [s for s in sources if s.get("handle", "").lower().replace("@", "") != handle]
                    config.SOURCES_FILE.write_text(json.dumps(new_sources, indent=2, ensure_ascii=False), encoding="utf-8")
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

            b_data = {"creators": []}
            if config.BLACKLIST_FILE.exists():
                try:
                    b_data = json.loads(config.BLACKLIST_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass

            blacklist = set(c.lower().replace("@", "") for c in b_data.get("creators", []))

            sources = []
            if config.SOURCES_FILE.exists():
                try:
                    sources = json.loads(config.SOURCES_FILE.read_text(encoding="utf-8"))
                except Exception:
                    pass

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
                config.BLACKLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
                config.BLACKLIST_FILE.write_text(json.dumps(b_data, indent=2), encoding="utf-8")
                config.SOURCES_FILE.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
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
    run_local_server()
