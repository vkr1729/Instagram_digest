#!/usr/bin/env bash
set -euo pipefail

# run_muse_max.sh: Executes Muse in full autonomous agentic mode at MAX reasoning effort
# Model: muse-spark-1.3-contributor
# Flags: --reasoning-effort max --no-foreign-personal-context --yolo

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MUSE_BIN="${HOME}/.local/bin/muse"

if [[ ! -x "${MUSE_BIN}" ]]; then
  echo "Error: muse binary not found at ${MUSE_BIN}" >&2
  exit 1
fi

if [[ $# -eq 0 ]]; then
  echo "Usage: $0 \"<prompt>\" or $0 --prompt-file <path>" >&2
  exit 1
fi

exec "${MUSE_BIN}" exec \
  --yolo \
  --workspace "${WORKSPACE_DIR}" \
  --model muse-spark-1.3-contributor \
  --reasoning-effort max \
  --no-foreign-personal-context \
  "$@"
