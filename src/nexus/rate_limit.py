from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Small single-process limiter; replace with shared storage before horizontal scaling."""

    def __init__(self, limit: int, window_seconds: int):
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 10_000:
                self._prune_locked(cutoff)
            return True

    def _prune_locked(self, cutoff: float) -> None:
        stale_keys = []
        for key, events in self._events.items():
            while events and events[0] <= cutoff:
                events.popleft()
            if not events:
                stale_keys.append(key)
        for key in stale_keys:
            self._events.pop(key, None)
