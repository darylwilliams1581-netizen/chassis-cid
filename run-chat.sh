#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

if [[ -z "${DRAYL_BRAIN:-}" ]]; then
  if [[ -f "$ROOT/school-pack.zip" ]]; then
    DRAYL_BRAIN="$ROOT/school-pack.zip"
  elif [[ -f "$ROOT/school-pack/manifest.json" ]]; then
    DRAYL_BRAIN="$ROOT/school-pack"
  else
    echo "No school-pack.zip found next to this file."
    echo "Run ./build-school-pack.sh first, or set DRAYL_BRAIN to your pack folder."
    exit 1
  fi
fi
export DRAYL_BRAIN

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

cd "$ROOT/chatbox"
exec "$PY" -m drayl_chat --brain "$DRAYL_BRAIN" "$@"
