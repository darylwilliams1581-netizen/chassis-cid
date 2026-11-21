#!/usr/bin/env python3
"""Build an offline school pack. Thin wrapper around `nursery.py pack`.

Example (from the drayl-p38 folder, works on Windows and Linux):

    python scripts/school_pack.py samples --trusted --queries 20 --output school-pack.zip
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.nursery import main as nursery_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return nursery_main(["pack", *args])


if __name__ == "__main__":
    sys.exit(main())
