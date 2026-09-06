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
