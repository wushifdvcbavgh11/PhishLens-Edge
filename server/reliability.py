"""Small, dependency-free reliability primitives for the analysis service."""

from collections import OrderedDict
from copy import deepcopy
from threading import RLock
from time import monotonic


POLICY_VERSION = "2026-10-13.1"
MODEL_VERSION = "siamese-multiref-associated-v3"


class LRUCache:
    """Bounded TTL cache; callers never share mutable response objects."""

    def __init__(self, capacity=256, ttl_seconds=120.0, clock=monotonic):
        if capacity < 1 or ttl_seconds <= 0:
            raise ValueError("cache capacity and TTL must be positive")
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = RLock()
        self._store = OrderedDict()

    def _expire(self):
        now = self._clock()
        for key, (expires, _) in list(self._store.items()):
            if expires <= now:
                del self._store[key]

    def get(self, key):
        with self._lock:
            self._expire()
            entry = self._store.get(key)
            if entry is None:
                return None
            self._store.move_to_end(key)
            return deepcopy(entry[1])

    def put(self, key, value):
        with self._lock:
            self._expire()
            self._store[key] = (self._clock() + self.ttl_seconds, deepcopy(value))
            self._store.move_to_end(key)
            while len(self._store) > self.capacity:
                self._store.popitem(last=False)

    def clear(self):
        with self._lock:
            self._store.clear()

    def __len__(self):
        with self._lock:
            self._expire()
            return len(self._store)
