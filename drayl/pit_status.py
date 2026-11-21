"""Shared busy flag for the sand pit (sleep mode or an index rebuild)."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator


class PitStatus:
    """Track when the pit is too busy to accept new writes immediately.

    Sleep mode and an index rebuild each take a slot. The teacher arm
    reads `busy` and, when it is true, parks incoming grains on a
    backing queue until the pit is free again.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._sleeping = 0
        self._rebuilding = 0
        self.on_idle: Callable[[], None] | None = None

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._sleeping > 0 or self._rebuilding > 0

    @property
    def reason(self) -> str | None:
        with self._lock:
            if self._sleeping > 0:
                return "sleep"
            if self._rebuilding > 0:
                return "index_rebuild"
            return None

    def begin_sleep(self) -> None:
        with self._lock:
            self._sleeping += 1

    def end_sleep(self) -> None:
        self._release("sleep")

    def begin_rebuild(self) -> None:
        with self._lock:
            self._rebuilding += 1

    def end_rebuild(self) -> None:
        self._release("rebuild")

    def _release(self, kind: str) -> None:
        callback = None
        with self._lock:
            if kind == "sleep":
                self._sleeping = max(0, self._sleeping - 1)
            else:
                self._rebuilding = max(0, self._rebuilding - 1)
            if self._sleeping == 0 and self._rebuilding == 0:
                callback = self.on_idle
        if callback is not None:
            callback()

    @contextmanager
    def sleep(self) -> Iterator[None]:
        self.begin_sleep()
        try:
            yield
        finally:
            self.end_sleep()

    @contextmanager
    def rebuild(self) -> Iterator[None]:
        self.begin_rebuild()
        try:
            yield
        finally:
            self.end_rebuild()
