"""
cookie_exporter.py — Decrypts Instagram cookies from Google Chrome on Linux via GNOME Keyring / DBus.
Outputs:
  - data/cookies.json (for Playwright and requests)
  - cookies.txt (Netscape format for yt-dlp)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("InstagramDigest.CookieExporter")


def get_chrome_secret_service_password() -> bytes:
    """Retrieve Chrome Safe Storage encryption key from Secret Service via DBus."""
    import dbus  # ImportError must propagate: main.py retries under /usr/bin/python3
    try:
        bus = dbus.SessionBus()
        secrets = bus.get_object("org.freedesktop.secrets", "/org/freedesktop/secrets")
        service = dbus.Interface(secrets, "org.freedesktop.Secret.Service")
        session_path = service.OpenSession("plain", "")[1]

        col = bus.get_object("org.freedesktop.secrets", "/org/freedesktop/secrets/collection/login")
        col_props = dbus.Interface(col, "org.freedesktop.DBus.Properties")
        items = col_props.Get("org.freedesktop.Secret.Collection", "Items")

        for item_path in items:
            item = bus.get_object("org.freedesktop.secrets", item_path)
            item_props = dbus.Interface(item, "org.freedesktop.DBus.Properties")
            label = str(item_props.Get("org.freedesktop.Secret.Item", "Label"))
            attrs = dict(item_props.Get("org.freedesktop.Secret.Item", "Attributes"))
            if label == "Chrome Safe Storage" and str(attrs.get("application", "")) == "chrome":
                sec_iface = dbus.Interface(item, "org.freedesktop.Secret.Item")
                secret = bytes(sec_iface.GetSecret(session_path)[2])
                logger.info("Successfully retrieved Chrome Safe Storage secret from Keyring.")
                return secret
    except Exception as exc:
        logger.warning("Could not query DBus for Chrome secret: %s", exc)

    logger.info("Falling back to default 'peanuts' password.")
    return b"peanuts"


def netscape_cookie_expiry(expires_utc: int | None) -> int:
    """Chrome expires_utc (µs since 1601-01-01) -> Netscape seconds (Unix).

    B4: without the 11644473600 offset every persistent cookie lands in
    ~year 2401 and dead cookies never expire for yt-dlp. Falsy input means
    a session cookie -> far-future sentinel (never expires).
    """
    if not expires_utc:
        return 2147483647
    return int(expires_utc / 1000000) - 11644473600


def decrypt_chrome_cookie(enc_bytes: bytes, key: bytes, iv: bytes, host: str = "") -> str:
    """Decrypt a v10/v11 Chrome cookie blob with the supplied Safe Storage key."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if not enc_bytes.startswith((b"v10", b"v11")):
        return ""
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    dec = cipher.decryptor()
    padded = dec.update(enc_bytes[3:]) + dec.finalize()
    if not padded:
        return ""
    pad_len = padded[-1]
    if pad_len < 1 or pad_len > 16 or len(padded) < 32 + pad_len:
        return ""
    if padded[-pad_len:] != bytes([pad_len]) * pad_len:
        return ""
    unpadded = padded[:-pad_len]
    # Linux Chrome prefixes SHA256(host_key): verifying it rejects wrong-key output.
    if host and unpadded[:32] != hashlib.sha256(host.encode("utf-8")).digest():
        return ""
    try:
        return unpadded[32:].decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _secure_write_text(path: Path, content: str) -> None:
    """Write a credential-bearing file atomically, readable only by its owner (0600).

    A torn cookies.json (power loss mid-write) fails _inject_cookies and aborts
    the next run, so the write goes temp + fsync + os.replace with a directory
    fsync (PY-P1-10). The temp is created in the target directory so the
    replace is atomic on the same filesystem.
    """
    import tempfile as _tempfile

    data = content.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = _tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.tmp-")
    try:
        # Restrict from the first byte: no world-readable window.
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        try:
            dir_fd = os.open(str(path.parent), os.O_DIRECTORY)
        except OSError:
            dir_fd = -1
        if dir_fd >= 0:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    os.chmod(path, 0o600)  # harden pre-existing files with looser modes


def export_instagram_cookies(output_dir: Path | None = None) -> dict[str, str]:
    """
    Decrypts Instagram cookies from Chrome Default profile and exports them to
    output_dir/cookies.json and output_dir/cookies.txt (also copied to root/cookies.txt).
    All three are written owner-only (0600). The root copy is required:
    extractor.py passes it to yt-dlp via --cookies.
    Returns dict of {cookie_name: cookie_value}.
    """
    if output_dir is None:
        output_dir = Path(__file__).parent / "data"
    output_dir.mkdir(parents=True, exist_ok=True)

    cookie_db_path = Path.home() / ".config/google-chrome/Default/Cookies"
    if not cookie_db_path.exists():
        logger.warning("Chrome Cookies database not found at %s", cookie_db_path)
        return {}

    chrome_secret = get_chrome_secret_service_password()
    key = hashlib.pbkdf2_hmac("sha1", chrome_secret, b"saltysalt", 1, 16)
    iv = b" " * 16

    cookies_dict: dict[str, str] = {}
    cookies_pw: list[dict[str, Any]] = []
    netscape_lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.haxx.se/rfc/cookie_spec.html",
        "",
    ]

    with tempfile.NamedTemporaryFile() as tmp:
        shutil.copy2(cookie_db_path, tmp.name)
        conn = sqlite3.connect(tmp.name)
        try:
            c = conn.cursor()
            c.execute(
                "SELECT host_key, name, path, expires_utc, is_secure, encrypted_value "
                "FROM cookies WHERE host_key LIKE '%instagram.com%'"
            )
            rows = c.fetchall()
        finally:
            conn.close()
        for host, name, path, expires, is_secure, enc_val in rows:
            enc_bytes = bytes(enc_val)
            val = decrypt_chrome_cookie(enc_bytes, key, iv, host)
            if val:
                cookies_dict[name] = val
                cookies_pw.append({
                    "name": name,
                    "value": val,
                    "domain": host,
                    "path": path,
                    "secure": bool(is_secure),
                    "httpOnly": True,
                })
                flag = "TRUE" if host.startswith(".") else "FALSE"
                sec = "TRUE" if is_secure else "FALSE"
                # B4: Chrome expires_utc is µs since 1601-01-01; Netscape wants
                # Unix seconds. Without the offset every cookie lands in ~2401
                # and dead cookies never expire for yt-dlp.
                exp = str(netscape_cookie_expiry(expires))
                netscape_lines.append(f"{host}\t{flag}\t{path}\t{sec}\t{exp}\t{name}\t{val}")

    json_path = output_dir / "cookies.json"
    txt_path = output_dir / "cookies.txt"
    root_txt_path = Path(__file__).parent / "cookies.txt"

    # Never clobber a good session with an empty/decrypted-zero result.
    # Midnight runs (locked keyring, missing DBus, rotated secret) must keep
    # last-known-good cookies so validation can still pass when you miss the
    # 6PM Chrome check — a failed refresh returns the existing dict untouched.
    if not cookies_dict.get("sessionid"):
        logger.warning(
            "Decrypted %d cookies with no sessionid; keeping existing %s untouched.",
            len(cookies_dict), json_path,
        )
        try:
            existing = json.loads(json_path.read_text(encoding="utf-8"))
            return dict(existing.get("cookies_dict", {}))
        except Exception:
            return {}


    _secure_write_text(
        json_path,
        json.dumps({"cookies_playwright": cookies_pw, "cookies_dict": cookies_dict}, indent=2),
    )
    txt_content = "\n".join(netscape_lines) + "\n"
    _secure_write_text(txt_path, txt_content)
    _secure_write_text(root_txt_path, txt_content)

    logger.info("Exported %d Instagram cookies to %s and %s", len(cookies_dict), json_path, txt_path)
    return cookies_dict


if __name__ == "__main__":
    cookies = export_instagram_cookies()
    print(f"Decrypted {len(cookies)} cookies: {list(cookies.keys())}")
