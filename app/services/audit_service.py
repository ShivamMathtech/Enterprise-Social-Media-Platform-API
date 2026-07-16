from __future__ import annotations

from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get('x-forwarded-for')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.client.host if request.client else None


def write_audit(
    db: Session,
    action: str,
    request: Request | None = None,
    *,
    actor_user_id: str | None = None,
    organization_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    outcome: str = 'success',
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> AuditLog:
    record = AuditLog(
        actor_user_id=actor_user_id,
        organization_id=organization_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        outcome=outcome,
        ip_address=client_ip(request),
        user_agent=request.headers.get('user-agent') if request else None,
        request_id=getattr(request.state, 'request_id', None) if request else None,
        metadata_json=metadata,
    )
    db.add(record)
    if commit:
        db.commit()
        db.refresh(record)
    return record
