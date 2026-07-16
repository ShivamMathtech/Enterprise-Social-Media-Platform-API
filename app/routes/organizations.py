from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user, require_org_roles
from app.models import Invitation, Membership, Organization, Role, User
from app.schemas import (
    APIMessage,
    InvitationAccept,
    InvitationCreate,
    InvitationRead,
    MembershipRead,
    MembershipUpdate,
    OrganizationCreate,
    OrganizationRead,
    OrganizationUpdate,
)
from app.security import secure_token, sha256
from app.services.audit_service import write_audit
from app.services.email_service import send_invitation_email
from app.services.auth_service import utcnow

router = APIRouter(tags=['Organizations'])
settings = get_settings()


def organization_read(db: Session, org: Organization, user_id: str | None = None) -> OrganizationRead:
    count = db.scalar(select(func.count(Membership.id)).where(Membership.organization_id == org.id, Membership.status == 'active')) or 0
    role_name = None
    if user_id:
        membership = db.scalar(select(Membership).where(Membership.organization_id == org.id, Membership.user_id == user_id))
        role = db.get(Role, membership.role_id) if membership else None
        role_name = role.name if role else None
    data = {column.name: getattr(org, column.name) for column in Organization.__table__.columns if column.name in OrganizationRead.model_fields}
    return OrganizationRead(**data, member_count=count, my_role=role_name)


def membership_read(db: Session, membership: Membership) -> MembershipRead:
    user = db.get(User, membership.user_id)
    role = db.get(Role, membership.role_id)
    if not user or not role:
        raise HTTPException(status_code=500, detail='Membership references missing user or role')
    return MembershipRead(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        email=user.email,
        username=user.username,
        role_id=role.id,
        role_name=role.name,
        status=membership.status,
        joined_at=membership.joined_at,
    )


@router.post('/organizations', response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create_organization(
    payload: OrganizationCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Organization).where(Organization.slug == payload.slug)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Organization slug already exists')
    owner_role = db.scalar(select(Role).where(Role.name == 'organization_owner', Role.scope == 'organization'))
    if not owner_role:
        raise HTTPException(status_code=500, detail='Organization owner role is not configured')
    org = Organization(name=payload.name, slug=payload.slug, created_by=user.id)
    db.add(org)
    db.flush()
    db.add(Membership(organization_id=org.id, user_id=user.id, role_id=owner_role.id))
    write_audit(db, 'organization.created', request, actor_user_id=user.id, organization_id=org.id, resource_type='organization', resource_id=org.id)
    db.commit()
    db.refresh(org)
    return organization_read(db, org, user.id)


@router.get('/organizations', response_model=list[OrganizationRead])
def list_organizations(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    orgs = list(
        db.scalars(
            select(Organization)
            .join(Membership, Membership.organization_id == Organization.id)
            .where(Membership.user_id == user.id, Membership.status == 'active')
            .order_by(Organization.name)
        ).all()
    )
    return [organization_read(db, org, user.id) for org in orgs]


@router.get('/organizations/{organization_id}', response_model=OrganizationRead)
def get_organization(
    organization_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = db.scalar(select(Membership).where(Membership.organization_id == organization_id, Membership.user_id == user.id, Membership.status == 'active'))
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organization not found')
    org = db.get(Organization, organization_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organization not found')
    return organization_read(db, org, user.id)


@router.patch('/organizations/{organization_id}', response_model=OrganizationRead)
def update_organization(
    organization_id: str,
    payload: OrganizationUpdate,
    request: Request,
    _: Membership = Depends(require_org_roles('organization_owner', 'organization_admin')),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    org = db.get(Organization, organization_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organization not found')
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(org, key, value)
    write_audit(db, 'organization.updated', request, actor_user_id=user.id, organization_id=org.id, resource_type='organization', resource_id=org.id)
    db.commit()
    db.refresh(org)
    return organization_read(db, org, user.id)


@router.delete('/organizations/{organization_id}', response_model=APIMessage)
def suspend_organization(
    organization_id: str,
    request: Request,
    _: Membership = Depends(require_org_roles('organization_owner')),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    org = db.get(Organization, organization_id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Organization not found')
    org.status = 'suspended'
    write_audit(db, 'organization.suspended', request, actor_user_id=user.id, organization_id=org.id, resource_type='organization', resource_id=org.id)
    db.commit()
    return APIMessage(message='Organization suspended')


@router.get('/organizations/{organization_id}/members', response_model=list[MembershipRead])
def list_members(
    organization_id: str,
    _: Membership = Depends(require_org_roles('organization_owner', 'organization_admin', 'organization_member')),
    db: Session = Depends(get_db),
):
    records = list(db.scalars(select(Membership).where(Membership.organization_id == organization_id).order_by(Membership.joined_at)).all())
    return [membership_read(db, record) for record in records]


@router.post('/organizations/{organization_id}/invitations', response_model=InvitationRead, status_code=status.HTTP_201_CREATED)
def create_invitation(
    organization_id: str,
    payload: InvitationCreate,
    request: Request,
    background_tasks: BackgroundTasks,
    _: Membership = Depends(require_org_roles('organization_owner', 'organization_admin')),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    role = db.get(Role, payload.role_id)
    if not role or role.scope != 'organization':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='A valid organization role is required')
    email = str(payload.email).lower()
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and db.scalar(select(Membership).where(Membership.organization_id == organization_id, Membership.user_id == existing_user.id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='User is already a member')
    raw_token = secure_token(32)
    invitation = Invitation(
        organization_id=organization_id,
        email=email,
        role_id=role.id,
        token_hash=sha256(raw_token),
        invited_by=user.id,
        expires_at=utcnow() + timedelta(hours=payload.expires_in_hours),
    )
    db.add(invitation)
    db.flush()
    org = db.get(Organization, organization_id)
    background_tasks.add_task(send_invitation_email, email, org.name if org else 'the organization', raw_token)
    write_audit(db, 'organization.invitation_created', request, actor_user_id=user.id, organization_id=organization_id, resource_type='invitation', resource_id=invitation.id, metadata={'email': email})
    db.commit()
    db.refresh(invitation)
    result = InvitationRead.model_validate(invitation)
    if settings.expose_debug_tokens and not settings.is_production:
        result.debug_invitation_token = raw_token
    return result


@router.get('/organizations/{organization_id}/invitations', response_model=list[InvitationRead])
def list_invitations(
    organization_id: str,
    _: Membership = Depends(require_org_roles('organization_owner', 'organization_admin')),
    db: Session = Depends(get_db),
):
    return list(db.scalars(select(Invitation).where(Invitation.organization_id == organization_id).order_by(Invitation.created_at.desc())).all())


@router.post('/invitations/accept', response_model=MembershipRead)
def accept_invitation(
    payload: InvitationAccept,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    invitation = db.scalar(select(Invitation).where(Invitation.token_hash == sha256(payload.token)))
    if not invitation or invitation.status != 'pending' or invitation.expires_at.replace(tzinfo=invitation.expires_at.tzinfo or utcnow().tzinfo) <= utcnow():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid or expired invitation')
    if invitation.email != user.email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Invitation was issued to another email')
    membership = db.scalar(select(Membership).where(Membership.organization_id == invitation.organization_id, Membership.user_id == user.id))
    if membership:
        membership.role_id = invitation.role_id
        membership.status = 'active'
    else:
        membership = Membership(organization_id=invitation.organization_id, user_id=user.id, role_id=invitation.role_id)
        db.add(membership)
        db.flush()
    invitation.status = 'accepted'
    invitation.accepted_at = utcnow()
    write_audit(db, 'organization.invitation_accepted', request, actor_user_id=user.id, organization_id=invitation.organization_id, resource_type='membership', resource_id=membership.id)
    db.commit()
    db.refresh(membership)
    return membership_read(db, membership)


@router.patch('/organizations/{organization_id}/members/{member_user_id}', response_model=MembershipRead)
def update_member(
    organization_id: str,
    member_user_id: str,
    payload: MembershipUpdate,
    request: Request,
    _: Membership = Depends(require_org_roles('organization_owner', 'organization_admin')),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = db.scalar(select(Membership).where(Membership.organization_id == organization_id, Membership.user_id == member_user_id))
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Membership not found')
    if payload.role_id:
        role = db.get(Role, payload.role_id)
        if not role or role.scope != 'organization':
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Valid organization role required')
        membership.role_id = role.id
    if payload.status:
        membership.status = payload.status
    write_audit(db, 'organization.member_updated', request, actor_user_id=user.id, organization_id=organization_id, resource_type='membership', resource_id=membership.id)
    db.commit()
    db.refresh(membership)
    return membership_read(db, membership)


@router.delete('/organizations/{organization_id}/members/{member_user_id}', response_model=APIMessage)
def remove_member(
    organization_id: str,
    member_user_id: str,
    request: Request,
    _: Membership = Depends(require_org_roles('organization_owner', 'organization_admin')),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = db.scalar(select(Membership).where(Membership.organization_id == organization_id, Membership.user_id == member_user_id))
    if not membership:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Membership not found')
    role = db.get(Role, membership.role_id)
    if role and role.name == 'organization_owner':
        owners = db.scalar(select(func.count(Membership.id)).join(Role, Role.id == Membership.role_id).where(Membership.organization_id == organization_id, Role.name == 'organization_owner', Membership.status == 'active')) or 0
        if owners <= 1:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Cannot remove the last organization owner')
    membership.status = 'removed'
    write_audit(db, 'organization.member_removed', request, actor_user_id=user.id, organization_id=organization_id, resource_type='membership', resource_id=membership.id)
    db.commit()
    return APIMessage(message='Member removed')
