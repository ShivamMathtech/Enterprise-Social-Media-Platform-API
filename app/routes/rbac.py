from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_permissions
from app.models import Permission, Role, User, UserRole
from app.schemas import APIMessage, PermissionRead, RoleCreate, RoleRead, RoleUpdate
from app.services.audit_service import write_audit
from app.services.rbac_service import replace_role_permissions, role_permissions

router = APIRouter(prefix='/rbac', tags=['RBAC'])


def role_read(db: Session, role: Role) -> RoleRead:
    data = {column.name: getattr(role, column.name) for column in Role.__table__.columns if column.name in RoleRead.model_fields}
    return RoleRead(**data, permissions=role_permissions(db, role.id))


@router.get('/permissions', response_model=list[PermissionRead])
def list_permissions(
    _: User = Depends(require_permissions('rbac.read')),
    db: Session = Depends(get_db),
):
    return list(db.scalars(select(Permission).order_by(Permission.code)).all())


@router.get('/roles', response_model=list[RoleRead])
def list_roles(
    _: User = Depends(require_permissions('rbac.read')),
    db: Session = Depends(get_db),
):
    return [role_read(db, role) for role in db.scalars(select(Role).order_by(Role.scope, Role.name)).all()]


@router.post('/roles', response_model=RoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    request: Request,
    actor: User = Depends(require_permissions('rbac.write')),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Role).where(Role.name == payload.name)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Role already exists')
    role = Role(name=payload.name, description=payload.description, scope=payload.scope)
    db.add(role)
    db.flush()
    try:
        replace_role_permissions(db, role, payload.permission_codes)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    write_audit(db, 'rbac.role_created', request, actor_user_id=actor.id, resource_type='role', resource_id=role.id)
    db.commit()
    db.refresh(role)
    return role_read(db, role)


@router.get('/roles/{role_id}', response_model=RoleRead)
def get_role(
    role_id: str,
    _: User = Depends(require_permissions('rbac.read')),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Role not found')
    return role_read(db, role)


@router.patch('/roles/{role_id}', response_model=RoleRead)
def update_role(
    role_id: str,
    payload: RoleUpdate,
    request: Request,
    actor: User = Depends(require_permissions('rbac.write')),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Role not found')
    if role.is_system and payload.description is None and payload.permission_codes is None:
        return role_read(db, role)
    if payload.description is not None:
        role.description = payload.description
    if payload.permission_codes is not None:
        try:
            replace_role_permissions(db, role, payload.permission_codes)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    write_audit(db, 'rbac.role_updated', request, actor_user_id=actor.id, resource_type='role', resource_id=role.id)
    db.commit()
    db.refresh(role)
    return role_read(db, role)


@router.delete('/roles/{role_id}', response_model=APIMessage)
def delete_role(
    role_id: str,
    request: Request,
    actor: User = Depends(require_permissions('rbac.write')),
    db: Session = Depends(get_db),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Role not found')
    if role.is_system:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='System roles cannot be deleted')
    if db.scalar(select(UserRole).where(UserRole.role_id == role.id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Role is assigned to users')
    write_audit(db, 'rbac.role_deleted', request, actor_user_id=actor.id, resource_type='role', resource_id=role.id)
    db.delete(role)
    db.commit()
    return APIMessage(message='Role deleted')


@router.post('/users/{user_id}/roles/{role_id}', response_model=APIMessage)
def assign_user_role(
    user_id: str,
    role_id: str,
    request: Request,
    actor: User = Depends(require_permissions('rbac.assign')),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    role = db.get(Role, role_id)
    if not target or not role or role.scope != 'global':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User or global role not found')
    existing = db.scalar(select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id))
    if not existing:
        db.add(UserRole(user_id=user_id, role_id=role_id, assigned_by=actor.id))
    write_audit(db, 'rbac.user_role_assigned', request, actor_user_id=actor.id, resource_type='user', resource_id=user_id, metadata={'role': role.name})
    db.commit()
    return APIMessage(message='Role assigned')


@router.delete('/users/{user_id}/roles/{role_id}', response_model=APIMessage)
def remove_user_role(
    user_id: str,
    role_id: str,
    request: Request,
    actor: User = Depends(require_permissions('rbac.assign')),
    db: Session = Depends(get_db),
):
    assignment = db.scalar(select(UserRole).where(UserRole.user_id == user_id, UserRole.role_id == role_id))
    if not assignment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Role assignment not found')
    role = db.get(Role, role_id)
    db.delete(assignment)
    write_audit(db, 'rbac.user_role_removed', request, actor_user_id=actor.id, resource_type='user', resource_id=user_id, metadata={'role': role.name if role else role_id})
    db.commit()
    return APIMessage(message='Role removed')
