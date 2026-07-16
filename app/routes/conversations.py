from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.chat_schemas import (
    ConversationCreate,
    ConversationMemberRead,
    ConversationRead,
    ConversationUpdate,
    InviteAccept,
    InviteCreate,
    InviteRead,
    MemberAdd,
    MemberUpdate,
    PaginatedConversations,
)
from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Conversation,
    ConversationInvite,
    ConversationMember,
    Membership,
    User,
    UserBlock,
    utcnow,
)
from app.realtime import hub
from app.security import secure_token, sha256
from app.services.audit_service import write_audit
from app.services.chat_service import (
    active_member,
    blocked_between,
    conversation_dict,
    member_dict,
    require_manager,
    require_member,
)

router = APIRouter(prefix='/chat', tags=['Conversations'])
settings = get_settings()


def _active_user(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if not user or user.status != 'active' or user.deleted_at is not None:
        raise HTTPException(status_code=422, detail=f'User {user_id} is not active')
    return user


@router.post('/conversations', response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: ConversationCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    participant_ids = [participant_id for participant_id in payload.participant_ids if participant_id != user.id]
    participants = [_active_user(db, participant_id) for participant_id in participant_ids]

    if payload.organization_id:
        creator_membership = db.scalar(
            select(Membership).where(
                Membership.organization_id == payload.organization_id,
                Membership.user_id == user.id,
                Membership.status == 'active',
            )
        )
        if not creator_membership:
            raise HTTPException(status_code=403, detail='Active organization membership required')

    direct_key = None
    if payload.type == 'direct':
        other = participants[0]
        if blocked_between(db, user.id, other.id):
            raise HTTPException(status_code=403, detail='Direct conversation is unavailable because one user blocked the other')
        direct_key = ':'.join(sorted([user.id, other.id]))
        existing = db.scalar(select(Conversation).where(Conversation.direct_key == direct_key, Conversation.deleted_at.is_(None)))
        if existing:
            member = db.scalar(
                select(ConversationMember).where(
                    ConversationMember.conversation_id == existing.id,
                    ConversationMember.user_id == user.id,
                )
            )
            if member and member.status != 'active':
                member.status = 'active'
                member.left_at = None
                db.commit()
            return conversation_dict(db, existing, user.id)

    max_members = min(payload.max_members, settings.max_group_members if payload.type != 'channel' else 5000)
    if len(participant_ids) + 1 > max_members:
        raise HTTPException(status_code=422, detail='Participant count exceeds conversation capacity')

    conversation = Conversation(
        organization_id=payload.organization_id,
        type=payload.type,
        direct_key=direct_key,
        title=payload.title,
        description=payload.description,
        avatar_url=payload.avatar_url,
        created_by=user.id,
        is_private=payload.is_private,
        max_members=max_members,
    )
    db.add(conversation)
    db.flush()
    db.add(ConversationMember(conversation_id=conversation.id, user_id=user.id, role='owner', invited_by=user.id))
    for participant in participants:
        db.add(ConversationMember(conversation_id=conversation.id, user_id=participant.id, role='member', invited_by=user.id))
    write_audit(
        db,
        'chat.conversation_created',
        request,
        actor_user_id=user.id,
        organization_id=payload.organization_id,
        resource_type='conversation',
        resource_id=conversation.id,
        metadata={'type': payload.type, 'participant_count': len(participants) + 1},
    )
    db.commit()
    db.refresh(conversation)
    event = {'type': 'conversation.created', 'conversation_id': conversation.id, 'data': conversation_dict(db, conversation, user.id)}
    for participant in [user, *participants]:
        await hub.broadcast_user(participant.id, event)
    return conversation_dict(db, conversation, user.id)


@router.get('/conversations', response_model=PaginatedConversations)
def list_conversations(
    type: str | None = Query(default=None, pattern='^(direct|group|channel)$'),
    status_filter: str = Query(default='active', alias='status', pattern='^(active|archived)$'),
    search: str | None = Query(default=None, max_length=180),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Conversation)
        .join(ConversationMember, ConversationMember.conversation_id == Conversation.id)
        .where(
            ConversationMember.user_id == user.id,
            ConversationMember.status == 'active',
            Conversation.status == status_filter,
            Conversation.deleted_at.is_(None),
        )
    )
    if type:
        stmt = stmt.where(Conversation.type == type)
    if search:
        stmt = stmt.where(or_(Conversation.title.ilike(f'%{search}%'), Conversation.description.ilike(f'%{search}%')))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = db.scalar(count_stmt) or 0
    conversations = list(
        db.scalars(
            stmt.order_by(Conversation.last_message_at.desc().nullslast(), Conversation.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )
    return {'items': [conversation_dict(db, item, user.id) for item in conversations], 'total': total, 'page': page, 'page_size': page_size}


@router.get('/conversations/{conversation_id}', response_model=ConversationRead)
def get_conversation(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_member(db, conversation_id, user.id)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    return conversation_dict(db, conversation, user.id)


@router.patch('/conversations/{conversation_id}', response_model=ConversationRead)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_manager(db, conversation_id, user.id, admin_only=True)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    if conversation.type == 'direct' and any(value is not None for value in [payload.title, payload.description, payload.avatar_url]):
        raise HTTPException(status_code=409, detail='Direct conversation metadata is derived from participants')
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(conversation, field, value)
    conversation.updated_at = utcnow()
    write_audit(db, 'chat.conversation_updated', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation.id)
    db.commit()
    event = {'type': 'conversation.updated', 'conversation_id': conversation.id, 'data': conversation_dict(db, conversation, user.id)}
    await hub.broadcast_room(conversation.id, event)
    return conversation_dict(db, conversation, user.id)


@router.post('/conversations/{conversation_id}/archive', response_model=ConversationRead)
async def archive_conversation(conversation_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id, admin_only=True)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.status = 'archived'
    conversation.archived_at = utcnow()
    write_audit(db, 'chat.conversation_archived', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation.id)
    db.commit()
    await hub.broadcast_room(conversation.id, {'type': 'conversation.archived', 'conversation_id': conversation.id, 'data': {}})
    return conversation_dict(db, conversation, user.id)


@router.post('/conversations/{conversation_id}/unarchive', response_model=ConversationRead)
async def unarchive_conversation(conversation_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id, admin_only=True)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.status = 'active'
    conversation.archived_at = None
    write_audit(db, 'chat.conversation_unarchived', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation.id)
    db.commit()
    await hub.broadcast_room(conversation.id, {'type': 'conversation.unarchived', 'conversation_id': conversation.id, 'data': {}})
    return conversation_dict(db, conversation, user.id)


@router.delete('/conversations/{conversation_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id, admin_only=True)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    conversation.status = 'deleted'
    conversation.deleted_at = utcnow()
    write_audit(db, 'chat.conversation_deleted', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation.id)
    db.commit()
    await hub.broadcast_room(conversation.id, {'type': 'conversation.deleted', 'conversation_id': conversation.id, 'data': {}})


@router.get('/conversations/{conversation_id}/members', response_model=list[ConversationMemberRead])
def list_members(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_member(db, conversation_id, user.id)
    members = list(
        db.scalars(
            select(ConversationMember)
            .where(ConversationMember.conversation_id == conversation_id)
            .order_by(ConversationMember.status, ConversationMember.joined_at)
        ).all()
    )
    return [member_dict(db, member) for member in members]


@router.post('/conversations/{conversation_id}/members', response_model=list[ConversationMemberRead], status_code=201)
async def add_members(
    conversation_id: str,
    payload: MemberAdd,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_manager(db, conversation_id, user.id, admin_only=True)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    if conversation.type == 'direct':
        raise HTTPException(status_code=409, detail='Direct conversations cannot add members')
    current_count = db.scalar(select(func.count(ConversationMember.id)).where(ConversationMember.conversation_id == conversation_id, ConversationMember.status == 'active')) or 0
    unique_ids = list(dict.fromkeys(payload.user_ids))
    if current_count + len(unique_ids) > conversation.max_members:
        raise HTTPException(status_code=409, detail='Conversation member capacity exceeded')
    added: list[ConversationMember] = []
    for member_id in unique_ids:
        if member_id == user.id:
            continue
        _active_user(db, member_id)
        existing = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_id))
        if existing:
            if existing.status == 'banned':
                raise HTTPException(status_code=409, detail=f'User {member_id} is banned from this conversation')
            existing.status = 'active'
            existing.role = payload.role
            existing.left_at = None
            added.append(existing)
        else:
            new_member = ConversationMember(conversation_id=conversation_id, user_id=member_id, role=payload.role, invited_by=user.id)
            db.add(new_member)
            added.append(new_member)
    write_audit(db, 'chat.members_added', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation_id, metadata={'user_ids': unique_ids})
    db.commit()
    for member in added:
        db.refresh(member)
        await hub.broadcast_user(member.user_id, {'type': 'conversation.member_added', 'conversation_id': conversation_id, 'data': member_dict(db, member)})
    await hub.broadcast_room(conversation_id, {'type': 'conversation.members_changed', 'conversation_id': conversation_id, 'data': {'added_user_ids': unique_ids}})
    return [member_dict(db, member) for member in added]


@router.patch('/conversations/{conversation_id}/members/{member_user_id}', response_model=ConversationMemberRead)
async def update_member(
    conversation_id: str,
    member_user_id: str,
    payload: MemberUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    actor = require_manager(db, conversation_id, user.id, admin_only=True)
    target = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_user_id))
    if not target:
        raise HTTPException(status_code=404, detail='Conversation member not found')
    if target.role == 'owner' and actor.user_id != target.user_id:
        raise HTTPException(status_code=409, detail='Ownership must be transferred before changing the owner')
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    if target.status != 'active':
        target.left_at = utcnow()
    write_audit(db, 'chat.member_updated', request, actor_user_id=user.id, resource_type='conversation_member', resource_id=target.id, metadata=payload.model_dump(exclude_unset=True))
    db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'conversation.member_updated', 'conversation_id': conversation_id, 'data': member_dict(db, target)})
    return member_dict(db, target)


@router.delete('/conversations/{conversation_id}/members/{member_user_id}', status_code=204)
async def remove_member(conversation_id: str, member_user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id, admin_only=True)
    target = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_user_id))
    if not target:
        raise HTTPException(status_code=404, detail='Conversation member not found')
    if target.role == 'owner':
        raise HTTPException(status_code=409, detail='The owner cannot be removed')
    target.status = 'removed'
    target.left_at = utcnow()
    write_audit(db, 'chat.member_removed', request, actor_user_id=user.id, resource_type='conversation_member', resource_id=target.id)
    db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'conversation.member_removed', 'conversation_id': conversation_id, 'data': {'user_id': member_user_id}})
    await hub.broadcast_user(member_user_id, {'type': 'conversation.removed', 'conversation_id': conversation_id, 'data': {}})


@router.post('/conversations/{conversation_id}/leave', status_code=204)
async def leave_conversation(conversation_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = require_member(db, conversation_id, user.id)
    if member.role == 'owner':
        other_admin = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id != user.id, ConversationMember.status == 'active', ConversationMember.role.in_(['owner', 'admin'])))
        if not other_admin:
            raise HTTPException(status_code=409, detail='Transfer ownership or appoint an admin before leaving')
    member.status = 'left'
    member.left_at = utcnow()
    write_audit(db, 'chat.member_left', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation_id)
    db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'conversation.member_left', 'conversation_id': conversation_id, 'data': {'user_id': user.id}})


@router.post('/conversations/{conversation_id}/invites', response_model=InviteRead, status_code=201)
def create_invite(
    conversation_id: str,
    payload: InviteCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_manager(db, conversation_id, user.id, admin_only=True)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    if conversation.type == 'direct':
        raise HTTPException(status_code=409, detail='Direct conversations do not support invite links')
    raw = secure_token(36)
    invite = ConversationInvite(
        conversation_id=conversation_id,
        token_hash=sha256(raw),
        created_by=user.id,
        max_uses=payload.max_uses,
        expires_at=utcnow() + timedelta(hours=payload.expires_in_hours) if payload.expires_in_hours else None,
    )
    db.add(invite)
    write_audit(db, 'chat.invite_created', request, actor_user_id=user.id, resource_type='conversation_invite', resource_id=invite.id)
    db.commit()
    db.refresh(invite)
    return {**invite.__dict__, 'invite_token': raw}


@router.post('/invites/accept', response_model=ConversationRead)
async def accept_invite(payload: InviteAccept, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    invite = db.scalar(select(ConversationInvite).where(ConversationInvite.token_hash == sha256(payload.token)))
    if not invite or invite.revoked_at is not None:
        raise HTTPException(status_code=404, detail='Invite is invalid or revoked')
    if invite.expires_at and invite.expires_at.replace(tzinfo=invite.expires_at.tzinfo or utcnow().tzinfo) <= utcnow():
        raise HTTPException(status_code=410, detail='Invite has expired')
    if invite.max_uses is not None and invite.uses >= invite.max_uses:
        raise HTTPException(status_code=410, detail='Invite usage limit reached')
    conversation = db.get(Conversation, invite.conversation_id)
    if not conversation or conversation.status != 'active':
        raise HTTPException(status_code=409, detail='Conversation is unavailable')
    current_count = db.scalar(select(func.count(ConversationMember.id)).where(ConversationMember.conversation_id == conversation.id, ConversationMember.status == 'active')) or 0
    if current_count >= conversation.max_members:
        raise HTTPException(status_code=409, detail='Conversation is full')
    membership = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation.id, ConversationMember.user_id == user.id))
    if membership and membership.status == 'banned':
        raise HTTPException(status_code=403, detail='You are banned from this conversation')
    if membership:
        membership.status = 'active'
        membership.left_at = None
    else:
        membership = ConversationMember(conversation_id=conversation.id, user_id=user.id, role='member', invited_by=invite.created_by)
        db.add(membership)
    invite.uses += 1
    write_audit(db, 'chat.invite_accepted', request, actor_user_id=user.id, resource_type='conversation', resource_id=conversation.id)
    db.commit()
    await hub.broadcast_room(conversation.id, {'type': 'conversation.member_joined', 'conversation_id': conversation.id, 'data': {'user_id': user.id}})
    return conversation_dict(db, conversation, user.id)


@router.delete('/conversations/{conversation_id}/invites/{invite_id}', status_code=204)
def revoke_invite(conversation_id: str, invite_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id, admin_only=True)
    invite = db.get(ConversationInvite, invite_id)
    if not invite or invite.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail='Invite not found')
    invite.revoked_at = utcnow()
    write_audit(db, 'chat.invite_revoked', request, actor_user_id=user.id, resource_type='conversation_invite', resource_id=invite.id)
    db.commit()
