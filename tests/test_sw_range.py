"""test_sw_range.py — Executes the shipped templates/sw.js range logic in Node
and asserts RFC 7233 behavior (P0-4). Fails if sw.js regresses to clamping.
"""
import re
import subprocess
from pathlib import Path

import pytest

SW_PATH = Path(__file__).resolve().parent.parent / "templates" / "sw.js"

HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[1], 'utf8');
const start = src.indexOf('// RFC 7233 range handling');
const end = src.indexOf("self.addEventListener('fetch'");
if (start < 0 || end < 0 || end < start) { console.error('RANGE_BLOCK_NOT_FOUND'); process.exit(2); }
const block = src.slice(start, end);
class FakeBlob {
  constructor(size) { this.size = size; }
  slice(s, e) { this._s = s; this._e = e; return { size: e - s }; }
}
class FakeResponse {
  constructor(body, init) { this.body = body; this.init = init; }
}
const fn = new Function('Blob', 'Response', block + '\nreturn { createPartialBlobResponse, rangeNotSatisfiable };');
const { createPartialBlobResponse } = fn(FakeBlob, FakeResponse);
const cases = JSON.parse(process.argv[2]);
const out = cases.map(([size, hdr]) => {
  const r = createPartialBlobResponse(new FakeBlob(size), hdr, 'video/mp4');
  const h = r.init.headers || {};
  return { status: r.init.status, range: h['Content-Range'], len: h['Content-Length'] };
});
console.log(JSON.stringify(out));
"""


def _run_cases(cases):
    proc = subprocess.run(
        ["node", "-e", HARNESS, str(SW_PATH), __import__("json").dumps(cases)],
        capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, f"node harness failed: {proc.stderr}"
    return __import__("json").loads(proc.stdout)


def test_sw_range_rfc7233_matrix():
    N = 1000
    cases = [
        [N, "bytes=0-99"],      # closed
        [N, "bytes=900-"],      # open-ended
        [N, "bytes=-100"],      # suffix
        [N, "bytes=0-"],        # full
        [N, "bytes=999-999"],   # last byte
        [N, "bytes=1000-"],     # start == size -> 416
        [N, "bytes=5000-6000"], # beyond EOF -> 416
        [N, "bytes=500-100"],   # inverted -> 416
        [N, "bytes=0-100,200-300"],  # multipart -> 416
        [N, "bytes=-0"],        # zero suffix -> 416
        [N, "garbage"],         # malformed -> 416
        [N, "bytes=0-9999"],    # end clamped, still 206
    ]
    got = _run_cases(cases)
    assert got[0] == {"status": 206, "range": "bytes 0-99/1000", "len": "100"}
    assert got[1] == {"status": 206, "range": "bytes 900-999/1000", "len": "100"}
    assert got[2] == {"status": 206, "range": "bytes 900-999/1000", "len": "100"}
    assert got[3] == {"status": 206, "range": "bytes 0-999/1000", "len": "1000"}
    assert got[4] == {"status": 206, "range": "bytes 999-999/1000", "len": "1"}
    for i in (5, 6, 7, 8, 9, 10):
        assert got[i]["status"] == 416, f"case {i} must be 416: {got[i]}"
        assert got[i]["range"] == "bytes */1000"
    assert got[11] == {"status": 206, "range": "bytes 0-999/1000", "len": "1000"}


def test_sw_source_has_no_clamping_fallback():
    src = SW_PATH.read_text(encoding="utf-8")
    assert "rangeNotSatisfiable" in src
    assert "Math.min(start, total - 1)" not in src
