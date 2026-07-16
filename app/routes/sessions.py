from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import RefreshSession, User
from app.schemas import APIMessage, SessionRead
from app.services.audit_service import write_audit
from app.services.auth_service import utcnow

router = APIRouter(prefix='/sessions', tags=['Sessions'])


@router.get('', response_model=list[SessionRead])
def list_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_id = getattr(request.state, 'session_id', None)
    records = list(
        db.scalars(
            select(RefreshSession)
            .where(RefreshSession.user_id == user.id)
            .order_by(RefreshSession.created_at.desc())
        ).all()
    )
    result = []
    for record in records:
        item = SessionRead.model_validate(record)
        item.current = record.id == current_id
        result.append(item)
    return result


@router.delete('/{session_id}', response_model=APIMessage)
def revoke_session(
    session_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = db.get(RefreshSession, session_id)
    if not record or record.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Session not found')
    if record.revoked_at is None:
        record.revoked_at = utcnow()
        record.revoke_reason = 'user_revoked'
    write_audit(db, 'session.revoked', request, actor_user_id=user.id, resource_type='session', resource_id=record.id)
    db.commit()
    return APIMessage(message='Session revoked')


@router.delete('', response_model=APIMessage)
def revoke_other_sessions(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    current_id = getattr(request.state, 'session_id', None)
    db.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.id != current_id,
        )
        .values(revoked_at=utcnow(), revoke_reason='other_sessions_revoked')
    )
    write_audit(db, 'session.others_revoked', request, actor_user_id=user.id)
    db.commit()
    return APIMessage(message='Other sessions revoked')
