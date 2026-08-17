"""SHA256-keyed API response cache.

Caches external API responses (UniProt, IEDB, NCBI, EBI, AlphaFold) keyed by
a deterministic hash of the request parameters. Reduces redundant calls to
rate-limited services and speeds up retried pipeline steps.

Cache key format: ``{hash(query_string)}`` — a hex SHA256 digest truncated to
16 chars. TTL defaults to 1 hour; override per-query with ``ttl`` kwarg.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Optional

_CACHE_DIR = os.environ.get("MEV_API_CACHE", "/tmp/mev-api-cache")
_DEFAULT_TTL = 3600  # 1 hour


def _cache_key(namespace: str, params: dict[str, Any]) -> str:
    """Build a deterministic cache key from namespace + params."""
    raw = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.sha256(f"{namespace}:{raw}".encode()).hexdigest()[:16]
    return f"{namespace}:{h}"


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, key)


def _read_cache(key: str, ttl: int = _DEFAULT_TTL) -> Optional[Any]:
    """Read a cached JSON value if it exists and is fresh."""
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        age = time.time() - os.path.getmtime(path)
        if age > ttl:
            return None
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(key: str, value: Any) -> None:
    """Write a JSON value to the cache."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _cache_path(key)
    try:
        with open(path, "w") as f:
            json.dump(value, f)
    except OSError:
        pass


def cached_api_call(
    namespace: str,
    params: dict[str, Any],
    fetcher,
    *,
    ttl: int = _DEFAULT_TTL,
) -> Any:
    """Call ``fetcher()`` with SHA256-keyed caching.

    ``fetcher`` must be a zero-arg callable (or sync function) that returns
    the API response. The result is cached as JSON.
    """
    key = _cache_key(namespace, params)
    cached = _read_cache(key, ttl=ttl)
    if cached is not None:
        return cached

    result = fetcher()
    if result is not None:
        _write_cache(key, result)
    return result


async def cached_async_api_call(
    namespace: str,
    params: dict[str, Any],
    fetcher,
    *,
    ttl: int = _DEFAULT_TTL,
) -> Any:
    """Async version of :func:`cached_api_call`."""
    key = _cache_key(namespace, params)
    cached = _read_cache(key, ttl=ttl)
    if cached is not None:
        return cached

    result = await fetcher()
    if result is not None:
        _write_cache(key, result)
    return result
