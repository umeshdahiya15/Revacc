"""Redis-based lock for distributed step coordination.

Used by Celery workers to ensure only one worker processes a given job's
step at a time, preventing race conditions during concurrent pipeline runs.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import redis

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_client: Optional[redis.Redis] = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.from_url(_REDIS_URL)
    return _client


def acquire_lock(lock_key: str, owner: str, ttl: int = 300) -> bool:
    """Try to acquire a distributed lock. Returns True if acquired."""
    try:
        client = get_redis()
        return client.set(lock_key, owner, nx=True, ex=ttl)
    except (redis.ConnectionError, redis.TimeoutError):
        return False


def release_lock(lock_key: str, owner: str) -> bool:
    """Release a lock if owned by `owner`. Returns True if released."""
    try:
        client = get_redis()
        # Lua script: only delete if the value matches our owner
        lua_script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """
        return bool(client.eval(lua_script, 1, lock_key, owner))
    except (redis.ConnectionError, redis.TimeoutError):
        return False


class DistributedLock:
    """Context manager for distributed locks."""

    def __init__(self, lock_key: str, owner: Optional[str] = None, ttl: int = 300):
        self.lock_key = lock_key
        self.owner = owner or f"worker-{time.time()}"
        self.ttl = ttl
        self._acquired = False

    def __enter__(self) -> "DistributedLock":
        self._acquired = acquire_lock(self.lock_key, self.owner, self.ttl)
        return self

    def __exit__(self, *args) -> None:
        if self._acquired:
            release_lock(self.lock_key, self.owner)
