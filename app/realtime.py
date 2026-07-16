from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

from app.config import get_settings

settings = get_settings()


@dataclass
class ConnectionState:
    user_id: str
    rooms: set[str] = field(default_factory=set)


class RealtimeHub:
    """Process-local connection registry with optional Redis fan-out.

    Local delivery always works. When REDIS_URL is configured, each API replica
    publishes events to Redis so WebSocket clients connected to other replicas
    receive the same event stream.
    """

    def __init__(self) -> None:
        self.instance_id = str(uuid.uuid4())
        self.connections: dict[WebSocket, ConnectionState] = {}
        self.user_sockets: dict[str, set[WebSocket]] = {}
        self.room_sockets: dict[str, set[WebSocket]] = {}
        self.lock = asyncio.Lock()
        self.redis = None
        self.listener_task: asyncio.Task | None = None
        self.channel = 'enterprise-chat:events'

    async def start(self) -> None:
        if not settings.redis_url or self.listener_task:
            return
        try:
            import redis.asyncio as redis
            self.redis = redis.from_url(settings.redis_url, encoding='utf-8', decode_responses=True)
            await self.redis.ping()
            self.listener_task = asyncio.create_task(self._listen())
        except Exception:
            self.redis = None
            self.listener_task = None

    async def stop(self) -> None:
        if self.listener_task:
            self.listener_task.cancel()
            try:
                await self.listener_task
            except BaseException:
                pass
            self.listener_task = None
        if self.redis:
            await self.redis.aclose()
            self.redis = None

    async def _listen(self) -> None:
        assert self.redis is not None
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.channel)
        try:
            async for item in pubsub.listen():
                if item.get('type') != 'message':
                    continue
                try:
                    envelope = json.loads(item['data'])
                except Exception:
                    continue
                if envelope.get('origin') == self.instance_id:
                    continue
                target = envelope.get('target')
                target_id = envelope.get('target_id')
                event = envelope.get('event') or {}
                if target == 'room' and target_id:
                    await self._broadcast_room_local(target_id, event)
                elif target == 'user' and target_id:
                    await self._broadcast_user_local(target_id, event)
        finally:
            await pubsub.unsubscribe(self.channel)
            await pubsub.close()

    async def connect(self, websocket: WebSocket, user_id: str, room_ids: list[str]) -> None:
        await websocket.accept()
        state = ConnectionState(user_id=user_id, rooms=set(room_ids))
        async with self.lock:
            self.connections[websocket] = state
            self.user_sockets.setdefault(user_id, set()).add(websocket)
            for room_id in room_ids:
                self.room_sockets.setdefault(room_id, set()).add(websocket)

    async def disconnect(self, websocket: WebSocket) -> str | None:
        async with self.lock:
            state = self.connections.pop(websocket, None)
            if not state:
                return None
            user_set = self.user_sockets.get(state.user_id)
            if user_set:
                user_set.discard(websocket)
                if not user_set:
                    self.user_sockets.pop(state.user_id, None)
            for room_id in state.rooms:
                room_set = self.room_sockets.get(room_id)
                if room_set:
                    room_set.discard(websocket)
                    if not room_set:
                        self.room_sockets.pop(room_id, None)
            return state.user_id

    async def subscribe(self, websocket: WebSocket, room_id: str) -> None:
        async with self.lock:
            state = self.connections.get(websocket)
            if not state:
                return
            state.rooms.add(room_id)
            self.room_sockets.setdefault(room_id, set()).add(websocket)

    async def unsubscribe(self, websocket: WebSocket, room_id: str) -> None:
        async with self.lock:
            state = self.connections.get(websocket)
            if state:
                state.rooms.discard(room_id)
            room_set = self.room_sockets.get(room_id)
            if room_set:
                room_set.discard(websocket)
                if not room_set:
                    self.room_sockets.pop(room_id, None)

    def is_user_online(self, user_id: str) -> bool:
        return bool(self.user_sockets.get(user_id))

    async def send(self, websocket: WebSocket, event: dict[str, Any]) -> bool:
        try:
            await websocket.send_json(jsonable_encoder(event))
            return True
        except Exception:
            await self.disconnect(websocket)
            return False

    async def _broadcast_room_local(self, room_id: str, event: dict[str, Any], exclude: WebSocket | None = None) -> None:
        sockets = list(self.room_sockets.get(room_id, set()))
        await asyncio.gather(*(self.send(ws, event) for ws in sockets if ws is not exclude), return_exceptions=True)

    async def _broadcast_user_local(self, user_id: str, event: dict[str, Any]) -> None:
        sockets = list(self.user_sockets.get(user_id, set()))
        await asyncio.gather(*(self.send(ws, event) for ws in sockets), return_exceptions=True)

    async def broadcast_room(self, room_id: str, event: dict[str, Any], exclude: WebSocket | None = None) -> None:
        await self._broadcast_room_local(room_id, event, exclude=exclude)
        if self.redis:
            await self.redis.publish(
                self.channel,
                json.dumps({'origin': self.instance_id, 'target': 'room', 'target_id': room_id, 'event': event}, default=str),
            )

    async def broadcast_user(self, user_id: str, event: dict[str, Any]) -> None:
        await self._broadcast_user_local(user_id, event)
        if self.redis:
            await self.redis.publish(
                self.channel,
                json.dumps({'origin': self.instance_id, 'target': 'user', 'target_id': user_id, 'event': event}, default=str),
            )


hub = RealtimeHub()
