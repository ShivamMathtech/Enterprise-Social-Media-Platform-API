from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permissions
from app.models import RefreshSession, User
from app.schemas import APIMessage, PaginatedUsers, UserAdminUpdate, UserRead
from app.services.audit_service import write_audit
from app.services.auth_service import user_to_read, utcnow

router = APIRouter(prefix='/admin', tags=['Administration'])


@router.get('/users', response_model=PaginatedUsers)
def list_users(
    search: str | None = None,
    user_status: str | None = Query(default=None, alias='status'),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: User = Depends(require_permissions('users.read')),
    db: Session = Depends(get_db),
):
    filters = [User.deleted_at.is_(None)]
    if search:
        term = f'%{search.lower()}%'
        filters.append(or_(User.email.ilike(term), User.username.ilike(term), User.first_name.ilike(term), User.last_name.ilike(term)))
    if user_status:
        filters.append(User.status == user_status)
    total = db.scalar(select(func.count(User.id)).where(*filters)) or 0
    users = list(db.scalars(select(User).where(*filters).order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all())
    return PaginatedUsers(items=[user_to_read(db, user) for user in users], total=total, page=page, page_size=page_size)


@router.get('/users/{user_id}', response_model=UserRead)
def get_user(
    user_id: str,
    _: User = Depends(require_permissions('users.read')),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    return user_to_read(db, user)


@router.patch('/users/{user_id}/status', response_model=UserRead)
def update_user_status(
    user_id: str,
    payload: UserAdminUpdate,
    request: Request,
    actor: User = Depends(require_permissions('users.write')),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    if user.id == actor.id and payload.status in {'disabled', 'locked'}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='You cannot lock or disable your own account')
    old = user.status
    user.status = payload.status
    if payload.status == 'active':
        user.failed_login_count = 0
        user.locked_until = None
    if payload.status in {'locked', 'disabled'}:
        db.execute(update(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)).values(revoked_at=utcnow(), revoke_reason=f'admin_{payload.status}'))
    write_audit(db, 'admin.user_status_changed', request, actor_user_id=actor.id, resource_type='user', resource_id=user.id, metadata={'from': old, 'to': payload.status})
    db.commit()
    db.refresh(user)
    return user_to_read(db, user)


@router.post('/users/{user_id}/unlock', response_model=UserRead)
def unlock_user(
    user_id: str,
    request: Request,
    actor: User = Depends(require_permissions('users.write')),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    user.status = 'active' if user.email_verified else 'pending_verification'
    user.failed_login_count = 0
    user.locked_until = None
    write_audit(db, 'admin.user_unlocked', request, actor_user_id=actor.id, resource_type='user', resource_id=user.id)
    db.commit()
    db.refresh(user)
    return user_to_read(db, user)


@router.delete('/users/{user_id}', response_model=APIMessage)
def delete_user(
    user_id: str,
    request: Request,
    actor: User = Depends(require_permissions('users.delete')),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    if user.id == actor.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='You cannot delete your own account')
    user.status = 'deleted'
    user.deleted_at = utcnow()
    db.execute(update(RefreshSession).where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None)).values(revoked_at=utcnow(), revoke_reason='user_deleted'))
    write_audit(db, 'admin.user_deleted', request, actor_user_id=actor.id, resource_type='user', resource_id=user.id)
    db.commit()
    return APIMessage(message='User soft-deleted')
