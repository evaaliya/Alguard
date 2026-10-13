"""In-memory token-bucket rate limiter, per key (owner).

Guards the Tier-1 agent channel against a hostile / prompt-injected agent that
floods `attempt_purchase`. Coarse but real: it bounds request *volume* per user,
independently of the risk engine's frequency-anomaly flag (which detects bursts
but does not throttle them).
"""
import threading
import time
from typing import Dict


class TokenBucket:
    """A single bucket. `capacity` tokens refilled at `refill_per_sec` tokens/second."""

    def __init__(self, capacity: float, refill_per_sec: float):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self.tokens = capacity
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill_per_sec)
            self.last = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True
            return False


class RateLimiter:
    """A set of per-key token buckets, created lazily and thread-safe."""

    def __init__(self, capacity: float, refill_per_sec: float):
        self.capacity = capacity
        self.refill_per_sec = refill_per_sec
        self._buckets: Dict[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self.capacity, self.refill_per_sec)
                self._buckets[key] = bucket
        return bucket.allow()
