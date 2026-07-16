from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permissions
from app.models import AuditLog, User
from app.schemas import PaginatedAuditLogs

router = APIRouter(prefix='/audit-logs', tags=['Audit Logs'])


@router.get('', response_model=PaginatedAuditLogs)
def list_audit_logs(
    action: str | None = None,
    actor_user_id: str | None = None,
    organization_id: str | None = None,
    outcome: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    _: User = Depends(require_permissions('audit.read')),
    db: Session = Depends(get_db),
):
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    if actor_user_id:
        filters.append(AuditLog.actor_user_id == actor_user_id)
    if organization_id:
        filters.append(AuditLog.organization_id == organization_id)
    if outcome:
        filters.append(AuditLog.outcome == outcome)
    total = db.scalar(select(func.count(AuditLog.id)).where(*filters)) or 0
    records = list(
        db.scalars(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return PaginatedAuditLogs(items=records, total=total, page=page, page_size=page_size)
