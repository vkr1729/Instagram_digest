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
    try:
        import dbus
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


def decrypt_chrome_cookie(enc_bytes: bytes, key: bytes, iv: bytes) -> str:
    """Decrypt v10 or v11 encrypted cookie blob."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if enc_bytes.startswith(b"v11"):
        ciphertext = enc_bytes[3:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        dec = cipher.decryptor().update(ciphertext) + cipher.decryptor().finalize()
        pad_len = dec[-1]
        unpadded = dec[:-pad_len]
        return unpadded[32:].decode("utf-8", errors="replace")
    elif enc_bytes.startswith(b"v10"):
        k10 = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
        cipher = Cipher(algorithms.AES(k10), modes.CBC(iv), backend=default_backend())
        dec = cipher.decryptor().update(enc_bytes[3:]) + cipher.decryptor().finalize()
        pad_len = dec[-1]
        unpadded = dec[:-pad_len]
        return unpadded[32:].decode("utf-8", errors="replace")
    return ""


def _secure_write_text(path: Path, content: str) -> None:
    """Write a credential-bearing file readable only by its owner (0600)."""
    path.write_text(content, encoding="utf-8")
    os.chmod(path, 0o600)


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
        c = conn.cursor()
        c.execute(
            "SELECT host_key, name, path, expires_utc, is_secure, encrypted_value "
            "FROM cookies WHERE host_key LIKE '%instagram.com%'"
        )
        rows = c.fetchall()
        for host, name, path, expires, is_secure, enc_val in rows:
            enc_bytes = bytes(enc_val)
            val = decrypt_chrome_cookie(enc_bytes, key, iv)
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
                exp = str(int(expires / 1000000) if expires else 2147483647)
                netscape_lines.append(f"{host}\t{flag}\t{path}\t{sec}\t{exp}\t{name}\t{val}")
        conn.close()

    json_path = output_dir / "cookies.json"
    txt_path = output_dir / "cookies.txt"
    root_txt_path = Path(__file__).parent / "cookies.txt"

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
