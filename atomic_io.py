"""
atomic_io.py — Crash-safe JSON persistence for Instagram Digest.

Guarantees:
- Readers never observe a truncated/torn write (write temp + fsync + os.replace).
- Data survives sudden power loss / process kill (file fsync + directory fsync).
- No temp-file litter on failure (cleanup in except path only).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def durable_write_json(path: str | Path, data: Any) -> None:
    """Atomically write JSON to *path* with fsync durability."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}.tmp-"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
        try:
            dir_fd = os.open(str(target.parent), os.O_DIRECTORY)
        except OSError:
            return
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
