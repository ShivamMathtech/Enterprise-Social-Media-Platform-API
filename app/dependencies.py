from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ApiKey, Membership, Role, User
from app.security import decode_jwt, sha256
from app.services.rbac_service import get_user_permissions

bearer = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name='X-API-Key', auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    api_key: str | None = Security(api_key_header),
    db: Session = Depends(get_db),
) -> User:
    user: User | None = None
    auth_kind = None

    if credentials:
        try:
            payload = decode_jwt(credentials.credentials, expected_type='access')
        except jwt.PyJWTError:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired access token')
        user = db.get(User, payload.get('sub'))
        request.state.session_id = payload.get('sid')
        auth_kind = 'bearer'
    elif api_key:
        prefix = api_key.split('.', 1)[0] if '.' in api_key else ''
        record = db.scalar(select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None)))
        if record and record.secret_hash == sha256(api_key):
            from datetime import datetime, timezone
            if record.expires_at and (record.expires_at.replace(tzinfo=timezone.utc) if record.expires_at.tzinfo is None else record.expires_at) <= datetime.now(timezone.utc):
                record = None
        if record:
            from app.models import utcnow
            record.last_used_at = utcnow()
            db.commit()
            user = db.get(User, record.user_id)
            auth_kind = 'api_key'
            request.state.api_key_scopes = set(record.scopes or [])
            request.state.api_key_id = record.id

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Authentication required')
    if user.status != 'active' or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='User account is not active')
    request.state.auth_kind = auth_kind
    request.state.current_user_id = user.id
    return user


def require_permissions(*required: str) -> Callable:
    async def dependency(
        request: Request,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        permissions = set(get_user_permissions(db, user.id))
        api_scopes = getattr(request.state, 'api_key_scopes', None)
        if api_scopes is not None:
            permissions = set(api_scopes) if '*' in permissions else permissions & api_scopes
        if '*' not in permissions and not set(required).issubset(permissions):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Insufficient permissions')
        return user
    return dependency


def require_org_roles(*allowed_roles: str) -> Callable:
    async def dependency(
        organization_id: str,
        user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> Membership:
        membership = db.scalar(
            select(Membership)
            .where(
                Membership.organization_id == organization_id,
                Membership.user_id == user.id,
                Membership.status == 'active',
            )
        )
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Organization membership required')
        role = db.get(Role, membership.role_id)
        if not role or role.name not in allowed_roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Organization role is not permitted')
        return membership
    return dependency
