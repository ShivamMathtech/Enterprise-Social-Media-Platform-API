from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import update
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import get_settings
from app.database import SessionLocal
from app.middleware import RateLimitMiddleware, RequestContextMiddleware
from app.realtime import hub
from app.models import UserPresence, utcnow
from app.routes import admin, api_keys, audit, auth, health, mfa, organizations, rbac, sessions
from app.routes import chat_users, conversations, messages, moderation, websocket
from app.routes import social_profiles, social_posts, social_communities, social_moderation

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        db.execute(update(UserPresence).where(UserPresence.status != 'offline').values(status='offline', last_seen_at=utcnow(), updated_at=utcnow()))
        db.commit()
    await hub.start()
    yield
    await hub.stop()


openapi_tags = [
    {'name': 'Authentication', 'description': 'Registration, login, rotating tokens, recovery and profile security.'},
    {'name': 'Multi-Factor Authentication', 'description': 'TOTP enrollment, recovery codes and MFA lifecycle.'},
    {'name': 'Sessions', 'description': 'Device/session visibility and revocation.'},
    {'name': 'Organizations', 'description': 'Multi-tenant organizations, invitations and memberships.'},
    {'name': 'RBAC', 'description': 'Roles, permissions and role assignments.'},
    {'name': 'API Keys', 'description': 'Scoped machine credentials with rotation and revocation.'},
    {'name': 'Administration', 'description': 'Enterprise user administration and account controls.'},
    {'name': 'Audit Logs', 'description': 'Security and administration event history.'},
    {'name': 'Social Profiles & Relationships', 'description': 'Profiles, privacy, follows, friendships and people discovery.'},
    {'name': 'Posts, Feed & Stories', 'description': 'Publishing, personalized feeds, comments, reactions, saved posts and stories.'},
    {'name': 'Pages & Groups', 'description': 'Creator pages, communities, membership and administration.'},
    {'name': 'Social Moderation & Analytics', 'description': 'Content reporting, enforcement, verification, trending topics and analytics.'},
    {'name': 'Conversations', 'description': 'Direct, group and channel lifecycle, memberships and invite links.'},
    {'name': 'Messages', 'description': 'Message history, attachments, reactions, receipts, pins and search.'},
    {'name': 'Chat Users', 'description': 'Presence, user discovery, blocking and notification preferences.'},
    {'name': 'Moderation', 'description': 'Reports, mute/ban actions and chat analytics.'},
    {'name': 'Realtime WebSocket', 'description': 'Bidirectional events for messages, typing, receipts, reactions and presence.'},
    {'name': 'System', 'description': 'Service metadata and health checks.'},
]

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        'Enterprise social media backend with secure identity, profiles, social graph, posts, feeds, stories, '
        'pages, groups, real-time chat, notifications, moderation, analytics and auditability.'
    ),
    docs_url='/docs' if settings.docs_enabled else None,
    redoc_url='/redoc' if settings.docs_enabled else None,
    openapi_url='/openapi.json' if settings.docs_enabled else None,
    openapi_tags=openapi_tags,
    lifespan=lifespan,
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)

Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
app.mount('/media', StaticFiles(directory=settings.upload_dir), name='media')


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            'error': {
                'code': 'validation_error',
                'message': 'Request validation failed',
                'details': exc.errors(),
                'request_id': getattr(request.state, 'request_id', None),
            }
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        message = detail.get('message', 'Request failed')
        code = detail.get('code', f'http_{exc.status_code}')
        details = detail.get('details')
    else:
        message = str(detail)
        code = f'http_{exc.status_code}'
        details = None
    return JSONResponse(
        status_code=exc.status_code,
        content={
            'error': {
                'code': code,
                'message': message,
                'details': details,
                'request_id': getattr(request.state, 'request_id', None),
            }
        },
        headers=exc.headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    details = {'type': type(exc).__name__, 'message': str(exc)} if settings.debug else None
    return JSONResponse(
        status_code=500,
        content={
            'error': {
                'code': 'internal_server_error',
                'message': 'An unexpected error occurred',
                'details': details,
                'request_id': getattr(request.state, 'request_id', None),
            }
        },
    )


app.include_router(health.router)
for rest_router in [
    auth.router,
    sessions.router,
    mfa.router,
    organizations.router,
    rbac.router,
    api_keys.router,
    admin.router,
    audit.router,
    conversations.router,
    messages.router,
    chat_users.router,
    moderation.router,
    social_profiles.router,
    social_posts.router,
    social_communities.router,
    social_moderation.router,
]:
    app.include_router(rest_router, prefix=settings.api_prefix)

app.include_router(websocket.router)
