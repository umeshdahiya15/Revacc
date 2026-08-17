"""WebSocket connection manager — one hub of subscribers per job id."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # job_id -> set of active sockets
        self._subscribers: dict[str, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._subscribers[job_id].add(websocket)

    async def disconnect(self, job_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._subscribers[job_id].discard(websocket)
            if not self._subscribers[job_id]:
                self._subscribers.pop(job_id, None)

    async def broadcast(self, job_id: str, payload: dict) -> int:
        """Send a JSON payload to every subscriber; return receivers count."""
        async with self._lock:
            sockets = list(self._subscribers.get(job_id, ()))
        dead: list[WebSocket] = []
        for socket in sockets:
            try:
                await socket.send_json(payload)
            except Exception:  # pragma: no cover - socket gone
                dead.append(socket)
        for socket in dead:
            await self.disconnect(job_id, socket)
        return len(sockets) - len(dead)


manager = ConnectionManager()