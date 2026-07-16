from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BackupCode, MFAMethod, OneTimeToken, RefreshSession, User
from app.schemas import TokenPair, UserRead
from app.security import (
    create_access_token,
    create_refresh_token,
    decrypt_secret,
    generate_backup_codes,
    hash_password,
    secure_token,
    sha256,
    verify_password,
    verify_totp,
)
from app.services.audit_service import client_ip, write_audit
from app.services.rbac_service import get_user_permissions, get_user_roles

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ensure_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def get_user_by_identifier(db: Session, identifier: str) -> User | None:
    normalized = identifier.lower().strip()
    return db.scalar(select(User).where(or_(User.email == normalized, User.username == normalized)))


def user_to_read(db: Session, user: User) -> UserRead:
    roles = [role.name for role in get_user_roles(db, user.id)]
    permissions = get_user_permissions(db, user.id)
    data = {column.name: getattr(user, column.name) for column in User.__table__.columns if column.name in UserRead.model_fields}
    return UserRead(**data, roles=roles, permissions=permissions)


def create_one_time_token(
    db: Session,
    user_id: str,
    purpose: str,
    expires_minutes: int,
    payload: dict | None = None,
) -> str:
    raw = secure_token(32)
    db.add(
        OneTimeToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=sha256(raw),
            payload=payload,
            expires_at=utcnow() + timedelta(minutes=expires_minutes),
        )
    )
    return raw


def consume_one_time_token(db: Session, raw_token: str, purpose: str) -> OneTimeToken:
    token = db.scalar(
        select(OneTimeToken).where(
            OneTimeToken.token_hash == sha256(raw_token),
            OneTimeToken.purpose == purpose,
        )
    )
    if not token or token.used_at is not None or ensure_aware(token.expires_at) <= utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid or expired token')
    token.used_at = utcnow()
    return token


def validate_login_user(db: Session, identifier: str, password: str, request: Request) -> User:
    user = get_user_by_identifier(db, identifier)
    generic = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid credentials')
    if not user or user.status == 'deleted':
        write_audit(db, 'auth.login_failed', request, outcome='failure', metadata={'identifier': identifier})
        db.commit()
        raise generic

    locked_until = ensure_aware(user.locked_until)
    if locked_until and locked_until > utcnow():
        write_audit(db, 'auth.login_blocked', request, actor_user_id=user.id, outcome='failure', metadata={'reason': 'locked'})
        db.commit()
        raise HTTPException(status_code=status.HTTP_423_LOCKED, detail='Account temporarily locked')

    if user.status in {'disabled', 'deleted'}:
        write_audit(db, 'auth.login_blocked', request, actor_user_id=user.id, outcome='failure', metadata={'reason': user.status})
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account is not active')

    if not verify_password(password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= settings.max_failed_logins:
            user.status = 'locked'
            user.locked_until = utcnow() + timedelta(minutes=settings.lockout_minutes)
        write_audit(
            db,
            'auth.login_failed',
            request,
            actor_user_id=user.id,
            outcome='failure',
            metadata={'failed_count': user.failed_login_count},
        )
        db.commit()
        raise generic

    if not user.email_verified or user.status == 'pending_verification':
        write_audit(db, 'auth.login_blocked', request, actor_user_id=user.id, outcome='failure', metadata={'reason': 'email_unverified'})
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Email verification required')

    user.failed_login_count = 0
    user.locked_until = None
    user.status = 'active'
    return user


def issue_token_pair(db: Session, user: User, request: Request, device_name: str | None = None) -> TokenPair:
    roles = [role.name for role in get_user_roles(db, user.id)]
    permissions = get_user_permissions(db, user.id)
    family_id = secrets.token_hex(16)
    session = RefreshSession(
        user_id=user.id,
        family_id=family_id,
        jti_hash='pending',
        ip_address=client_ip(request),
        user_agent=request.headers.get('user-agent'),
        device_name=device_name,
        expires_at=utcnow() + timedelta(days=settings.refresh_token_days),
    )
    db.add(session)
    db.flush()
    jti = secure_token(24)
    session.jti_hash = sha256(jti)
    refresh_token = create_refresh_token(user.id, family_id, session.id, jti)
    access_token = create_access_token(user.id, roles, permissions, session.id)
    user.last_login_at = utcnow()
    write_audit(db, 'auth.login_succeeded', request, actor_user_id=user.id, resource_type='session', resource_id=session.id)
    db.commit()
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_in=settings.access_token_minutes * 60,
        refresh_expires_in=settings.refresh_token_days * 86400,
    )


def rotate_refresh_token(db: Session, payload: dict, request: Request) -> TokenPair:
    session = db.scalar(select(RefreshSession).where(RefreshSession.id == payload.get('sid')))
    now = utcnow()
    if not session or session.user_id != payload.get('sub') or session.jti_hash != sha256(payload.get('jti', '')):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid refresh token')

    if session.revoked_at is not None:
        if session.replaced_by_id:
            db.execute(
                update(RefreshSession)
                .where(RefreshSession.family_id == session.family_id, RefreshSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason='refresh_token_reuse_detected')
            )
            write_audit(
                db,
                'auth.refresh_reuse_detected',
                request,
                actor_user_id=session.user_id,
                outcome='failure',
                metadata={'family_id': session.family_id},
            )
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Refresh token has been revoked')

    if ensure_aware(session.expires_at) <= now:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Refresh token has expired')

    user = db.get(User, session.user_id)
    if not user or user.status != 'active':
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='User account is not active')

    new_session = RefreshSession(
        user_id=user.id,
        family_id=session.family_id,
        jti_hash='pending',
        parent_id=session.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get('user-agent'),
        device_name=session.device_name,
        expires_at=now + timedelta(days=settings.refresh_token_days),
    )
    db.add(new_session)
    db.flush()
    jti = secure_token(24)
    new_session.jti_hash = sha256(jti)
    session.revoked_at = now
    session.revoke_reason = 'rotated'
    session.replaced_by_id = new_session.id
    session.last_used_at = now

    roles = [role.name for role in get_user_roles(db, user.id)]
    permissions = get_user_permissions(db, user.id)
    tokens = TokenPair(
        access_token=create_access_token(user.id, roles, permissions, new_session.id),
        refresh_token=create_refresh_token(user.id, session.family_id, new_session.id, jti),
        access_expires_in=settings.access_token_minutes * 60,
        refresh_expires_in=settings.refresh_token_days * 86400,
    )
    write_audit(db, 'auth.refresh_rotated', request, actor_user_id=user.id, resource_type='session', resource_id=new_session.id)
    db.commit()
    return tokens


def verify_mfa_code(db: Session, user: User, code: str) -> bool:
    method = db.scalar(
        select(MFAMethod).where(
            MFAMethod.user_id == user.id,
            MFAMethod.method == 'totp',
            MFAMethod.confirmed_at.is_not(None),
            MFAMethod.disabled_at.is_(None),
        )
    )
    if method and verify_totp(decrypt_secret(method.secret_encrypted), code):
        return True

    normalized = code.upper().strip()
    for record in db.scalars(
        select(BackupCode).where(BackupCode.user_id == user.id, BackupCode.used_at.is_(None))
    ).all():
        if secrets.compare_digest(record.code_hash, sha256(normalized)):
            record.used_at = utcnow()
            return True
    return False


def replace_backup_codes(db: Session, user_id: str) -> list[str]:
    db.query(BackupCode).filter(BackupCode.user_id == user_id).delete(synchronize_session=False)
    codes = generate_backup_codes()
    db.add_all([BackupCode(user_id=user_id, code_hash=sha256(code)) for code in codes])
    return codes
