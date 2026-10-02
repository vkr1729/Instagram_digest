# AGENTS.md — Operational & Testing Rules for Instagram Digest

This document outlines mandatory operational, development, and testing rules that every agent working on Instagram Digest must strictly observe.

---

## 1. Never Run Bare `python main.py` or `python main.py --sync`
Running `python main.py` or `python main.py --sync` without scoping flags triggers a **full production sync** across all tracked Instagram creators, downloads real videos, consumes Cloudflare R2 bandwidth/storage quota, deploys to GitHub Pages, updates production `seen_reel_ids.json` and `last_run.json`, and records durable resume markers.

### Required Testing Patterns:
- **For dry simulation of discovery & ranking:**
  ```bash
  .venv/bin/python main.py --dry-run
  ```
- **For fast scoped testing (few creators/short window):**
  ```bash
  .venv/bin/python main.py --dry-run --limit-per-creator 1 --days-back 1
  ```
- **For testing static site compilation without network or deployments:**
  ```bash
  .venv/bin/python main.py --build-only
  ```
- **For testing feed expansion safely:**
  ```bash
  .venv/bin/python main.py --expand 3 --dry-run
  ```

---

## 2. Never Leave Lingering Stalled Checkpoints in `data/`
- If a manual test run is stopped midway (via `Ctrl+C`, `SIGTERM`, or crash), ensure no lingering `sync_progress_*.json` or `expand_checkpoint_*.json` remains in `data/`:
  ```bash
  rm -f data/sync_progress_*.json data/expand_checkpoint_*.json data/expand_progress_*.json
  ```
- A strict **3-hour TTL safeguard** is now enforced in `main.py` and `resume_pending.sh`. Stalled checkpoints older than 3 hours are automatically retired/deleted and will never be auto-resumed.
- Interactive CLI cancellation (`Ctrl+C` / SIGINT) automatically cleans up transient checkpoints created during the cancelled session.

---

## 3. Weekly Pipeline Orchestration Precedence
- The weekly pipeline is orchestrated on **Saturday evenings at 18:00** by **`friday-overnight.service`** (invoking `~/Instagram_digest/run_friday_overnight.sh`), which executes:
  1. **Instagram Digest** first (`Instagram_digest/run_weekly.sh`).
  2. **TubeLM** second (`~/.tubelm/run_weekly.sh`).
  3. System power-off.
- **Do NOT re-enable `instagram-digest-resume.service`** under systemd user `default.target`. Resume is only triggered sequentially or on-demand with non-stale checkpoints.

---

## 4. Secret & Environment Protection
- Never view, edit, print, or expose `.env` credentials in chat, transcripts, or commit messages.
- Always inspect `.env.example` and `config.py` for schema, variable names, and defaults.
- Always check that `.gitignore` prevents secret and data leakages before committing.
