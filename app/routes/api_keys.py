from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import ApiKey, Membership, User
from app.schemas import APIMessage, ApiKeyCreate, ApiKeyCreated, ApiKeyRead
from app.security import generate_api_key
from app.services.audit_service import write_audit
from app.services.auth_service import utcnow

router = APIRouter(prefix='/api-keys', tags=['API Keys'])
settings = get_settings()


@router.post('', response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.organization_id:
        membership = db.scalar(
            select(Membership).where(
                Membership.organization_id == payload.organization_id,
                Membership.user_id == user.id,
                Membership.status == 'active',
            )
        )
        if not membership:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Organization membership required')
    raw, prefix, secret_hash = generate_api_key()
    days = payload.expires_in_days or settings.api_key_default_days
    record = ApiKey(
        user_id=user.id,
        organization_id=payload.organization_id,
        name=payload.name,
        prefix=prefix,
        secret_hash=secret_hash,
        scopes=sorted(set(payload.scopes)),
        expires_at=utcnow() + timedelta(days=days),
    )
    db.add(record)
    db.flush()
    write_audit(db, 'api_key.created', request, actor_user_id=user.id, organization_id=payload.organization_id, resource_type='api_key', resource_id=record.id)
    db.commit()
    db.refresh(record)
    return ApiKeyCreated(api_key=raw, record=ApiKeyRead.model_validate(record))


@router.get('', response_model=list[ApiKeyRead])
def list_api_keys(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list(db.scalars(select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())).all())


@router.delete('/{api_key_id}', response_model=APIMessage)
def revoke_api_key(
    api_key_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.get(ApiKey, api_key_id)
    if not record or record.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='API key not found')
    if record.revoked_at is None:
        record.revoked_at = utcnow()
    write_audit(db, 'api_key.revoked', request, actor_user_id=user.id, organization_id=record.organization_id, resource_type='api_key', resource_id=record.id)
    db.commit()
    return APIMessage(message='API key revoked')


@router.post('/{api_key_id}/rotate', response_model=ApiKeyCreated)
def rotate_api_key(
    api_key_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    old = db.get(ApiKey, api_key_id)
    if not old or old.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='API key not found')
    old.revoked_at = utcnow()
    raw, prefix, secret_hash = generate_api_key()
    replacement = ApiKey(
        user_id=old.user_id,
        organization_id=old.organization_id,
        name=f'{old.name} (rotated)',
        prefix=prefix,
        secret_hash=secret_hash,
        scopes=old.scopes,
        expires_at=old.expires_at,
    )
    db.add(replacement)
    db.flush()
    write_audit(db, 'api_key.rotated', request, actor_user_id=user.id, organization_id=old.organization_id, resource_type='api_key', resource_id=replacement.id, metadata={'replaces': old.id})
    db.commit()
    db.refresh(replacement)
    return ApiKeyCreated(api_key=raw, record=ApiKeyRead.model_validate(replacement))
