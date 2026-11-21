#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "D-RAYL Chat Box needs Python 3.10 or newer, and it is not installed."
  echo "Download it from https://www.python.org/downloads/"
  exit 1
fi

if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"; then
  echo "D-RAYL Chat Box needs Python 3.10 or newer."
  echo "Download it from https://www.python.org/downloads/"
  exit 1
fi

echo "Optional: pass --brain <school-pack-folder> or set DRAYL_BRAIN."
exec "$PY" -m drayl_chat "$@"
