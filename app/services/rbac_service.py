from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Permission, Role, RolePermission, UserRole


def get_user_roles(db: Session, user_id: str) -> list[Role]:
    stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.name)
    )
    return list(db.scalars(stmt).all())


def get_user_permissions(db: Session, user_id: str) -> list[str]:
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .where(UserRole.user_id == user_id)
        .distinct()
        .order_by(Permission.code)
    )
    return list(db.scalars(stmt).all())


def role_permissions(db: Session, role_id: str) -> list[str]:
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
        .order_by(Permission.code)
    )
    return list(db.scalars(stmt).all())


def replace_role_permissions(db: Session, role: Role, permission_codes: list[str]) -> None:
    db.query(RolePermission).filter(RolePermission.role_id == role.id).delete(synchronize_session=False)
    if permission_codes:
        permissions = list(db.scalars(select(Permission).where(Permission.code.in_(set(permission_codes)))).all())
        found = {permission.code for permission in permissions}
        missing = set(permission_codes) - found
        if missing:
            raise ValueError(f'Unknown permissions: {sorted(missing)}')
        db.add_all([RolePermission(role_id=role.id, permission_id=permission.id) for permission in permissions])
