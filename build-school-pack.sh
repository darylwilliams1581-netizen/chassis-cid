#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "Building a school pack needs Python 3.10 or newer, and it is not installed."
  echo "Download it from https://www.python.org/downloads/"
  exit 1
fi

if ! "$PY" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"; then
  echo "Building a school pack needs Python 3.10 or newer."
  echo "Download it from https://www.python.org/downloads/"
  exit 1
fi

"$PY" seed/scripts/school_pack.py textbooks --trusted --queries 20 --output school-pack.zip
echo
echo "Done. school-pack.zip is ready. Run ./run-chat.sh to boot the chat box from it."
