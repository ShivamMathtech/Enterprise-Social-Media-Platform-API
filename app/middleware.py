from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.config import get_settings

settings = get_settings()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get('x-request-id') or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers['X-Request-ID'] = request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        if settings.is_production:
            response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Redis fixed-window limiter with an in-memory fallback for local development."""

    def __init__(self, app):
        super().__init__(app)
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()
        self.redis = None
        if settings.redis_url:
            try:
                import redis.asyncio as redis
                self.redis = redis.from_url(settings.redis_url, encoding='utf-8', decode_responses=True)
            except Exception:
                self.redis = None

    async def _redis_allowed(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        assert self.redis is not None
        bucket = int(time.time()) // window
        redis_key = f'ratelimit:{key}:{bucket}'
        count = await self.redis.incr(redis_key)
        if count == 1:
            await self.redis.expire(redis_key, window + 1)
        return count <= limit, window

    async def _memory_allowed(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        now = time.monotonic()
        async with self.lock:
            bucket = self.hits[key]
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = max(1, int(window - (now - bucket[0])))
                return False, retry_after
            bucket.append(now)
        return True, window

    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith('/docs') or request.url.path.startswith('/openapi'):
            return await call_next(request)
        window = settings.rate_limit_window_seconds
        limit = settings.login_rate_limit_requests if request.url.path.endswith(('/auth/login', '/auth/token')) else settings.rate_limit_requests
        ip = request.headers.get('x-forwarded-for', '').split(',')[0].strip() or (request.client.host if request.client else 'unknown')
        key = f'{ip}:{request.url.path}'
        try:
            allowed, retry_after = await self._redis_allowed(key, limit, window) if self.redis is not None else await self._memory_allowed(key, limit, window)
        except Exception:
            allowed, retry_after = await self._memory_allowed(key, limit, window)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    'error': {
                        'code': 'rate_limit_exceeded',
                        'message': 'Too many requests',
                        'request_id': getattr(request.state, 'request_id', None),
                    }
                },
                headers={'Retry-After': str(retry_after)},
            )
        return await call_next(request)
