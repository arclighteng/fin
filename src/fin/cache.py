# cache.py
"""
Simple in-memory caching for expensive computations.

Used for pattern detection, classification, and report generation
to avoid redundant computations within a session.
"""
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class CacheEntry:
    """A cached value with expiration."""
    value: Any
    expires_at: float
    hits: int = 0


class SimpleCache:
    """
    Simple TTL-based in-memory cache.

    Not thread-safe - intended for single-request caching.
    """

    def __init__(self, default_ttl: float = 60.0, max_entries: int = 100):
        """
        Initialize cache.

        Args:
            default_ttl: Default time-to-live in seconds
            max_entries: Maximum cache entries before eviction
        """
        self._cache: dict[str, CacheEntry] = {}
        self._default_ttl = default_ttl
        self._max_entries = max_entries
        self._stats = {"hits": 0, "misses": 0}

    def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired."""
        entry = self._cache.get(key)
        if entry is None:
            self._stats["misses"] += 1
            return None

        if time.time() > entry.expires_at:
            del self._cache[key]
            self._stats["misses"] += 1
            return None

        entry.hits += 1
        self._stats["hits"] += 1
        return entry.value

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """Set value in cache with optional TTL."""
        if len(self._cache) >= self._max_entries:
            self._evict_oldest()

        expires_at = time.time() + (ttl or self._default_ttl)
        self._cache[key] = CacheEntry(value=value, expires_at=expires_at)

    def delete(self, key: str) -> None:
        """Remove key from cache."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Clear all cache entries."""
        self._cache.clear()

    def stats(self) -> dict:
        """Get cache statistics."""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0
        return {
            "entries": len(self._cache),
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "hit_rate": round(hit_rate, 3),
        }

    def _evict_oldest(self) -> None:
        """Evict oldest entries when cache is full."""
        # Evict 10% of entries
        to_evict = max(1, len(self._cache) // 10)
        sorted_keys = sorted(
            self._cache.keys(),
            key=lambda k: self._cache[k].expires_at
        )
        for key in sorted_keys[:to_evict]:
            del self._cache[key]


# Global cache instance for pattern detection
_pattern_cache = SimpleCache(default_ttl=300.0, max_entries=50)

# Global cache for report results
_report_cache = SimpleCache(default_ttl=60.0, max_entries=20)


def get_pattern_cache() -> SimpleCache:
    """Get the global pattern cache."""
    return _pattern_cache


def get_report_cache() -> SimpleCache:
    """Get the global report cache."""
    return _report_cache


def cache_key(*args, **kwargs) -> str:
    """Generate a cache key from arguments."""
    # Create a deterministic string from args/kwargs
    data = {
        "args": [str(a) for a in args],
        "kwargs": {k: str(v) for k, v in sorted(kwargs.items())},
    }
    serialized = json.dumps(data, sort_keys=True)
    return hashlib.md5(serialized.encode()).hexdigest()


def cached(cache: SimpleCache, ttl: Optional[float] = None):
    """
    Decorator to cache function results.

    Args:
        cache: Cache instance to use
        ttl: Optional TTL override

    Usage:
        @cached(get_pattern_cache(), ttl=300)
        def expensive_function(arg1, arg2):
            ...
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            key = f"{func.__name__}:{cache_key(*args, **kwargs)}"
            result = cache.get(key)
            if result is not None:
                return result
            result = func(*args, **kwargs)
            cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator


def invalidate_pattern_cache() -> None:
    """Invalidate pattern cache (e.g., after sync)."""
    _pattern_cache.clear()


def invalidate_report_cache() -> None:
    """Invalidate report cache (e.g., after data change)."""
    _report_cache.clear()


def get_cache_stats() -> dict:
    """Get statistics for all caches."""
    return {
        "pattern_cache": _pattern_cache.stats(),
        "report_cache": _report_cache.stats(),
    }
