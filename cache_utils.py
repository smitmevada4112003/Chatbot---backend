import time
import threading

class FastCache:
    """
    Lightweight, thread-safe in-memory cache with TTL.
    Prevents repeated expensive WAN queries to remote MySQL
    on rapid button clicks, tab switches, and concurrent requests.
    """
    def __init__(self, default_ttl=3.0):
        self._data = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    def get(self, key):
        with self._lock:
            if key in self._data:
                val, expire_at = self._data[key]
                if time.time() < expire_at:
                    return val
                del self._data[key]
        return None

    def set(self, key, value, ttl=None):
        with self._lock:
            expire_at = time.time() + (ttl if ttl is not None else self._default_ttl)
            self._data[key] = (value, expire_at)

    def invalidate(self, *keys):
        with self._lock:
            if not keys:
                self._data.clear()
            else:
                for k in keys:
                    self._data.pop(k, None)

# Shared cache instances
orders_cache = FastCache(default_ttl=3.0)
products_cache = FastCache(default_ttl=4.0)
dashboard_cache = FastCache(default_ttl=4.0)
