from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chat_schemas import (
    BlockRead,
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
    PaginatedNotifications,
    PresenceRead,
    PresenceUpdate,
)
from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    ConversationMember,
    Notification,
    NotificationPreference,
    User,
    UserBlock,
    UserPresence,
    utcnow,
)
from app.realtime import hub
from app.schemas import APIMessage, UserRead
from app.services.auth_service import user_to_read
from app.services.chat_service import require_member, user_display_name

router = APIRouter(prefix='/chat', tags=['Chat Users'])


@router.get('/users', response_model=list[UserRead])
def search_users(
    q: str = Query(min_length=2, max_length=100),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    users = list(
        db.scalars(
            select(User)
            .where(
                User.id != user.id,
                User.status == 'active',
                User.deleted_at.is_(None),
                (User.username.ilike(f'%{q}%') | User.email.ilike(f'%{q}%') | User.first_name.ilike(f'%{q}%') | User.last_name.ilike(f'%{q}%')),
            )
            .order_by(User.username)
            .limit(limit)
        ).all()
    )
    return [user_to_read(db, item) for item in users]


@router.get('/presence/{user_id}', response_model=PresenceRead)
def get_presence(user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target or target.status != 'active':
        raise HTTPException(status_code=404, detail='User not found')
    presence = db.get(UserPresence, user_id)
    if not presence:
        presence = UserPresence(user_id=user_id, status='offline')
        db.add(presence)
        db.commit()
        db.refresh(presence)
    response = PresenceRead.model_validate(presence, from_attributes=True).model_dump()
    if hub.is_user_online(user_id) and response['status'] == 'offline':
        response['status'] = 'online'
    return response


@router.put('/presence', response_model=PresenceRead)
async def update_presence(payload: PresenceUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    presence = db.get(UserPresence, user.id)
    if not presence:
        presence = UserPresence(user_id=user.id)
        db.add(presence)
    presence.status = payload.status
    presence.custom_status = payload.custom_status
    presence.last_seen_at = utcnow()
    presence.updated_at = utcnow()
    db.commit()
    db.refresh(presence)
    conversation_ids = list(db.scalars(select(ConversationMember.conversation_id).where(ConversationMember.user_id == user.id, ConversationMember.status == 'active')).all())
    event = {'type': 'presence.updated', 'data': PresenceRead.model_validate(presence, from_attributes=True).model_dump(mode='json')}
    for conversation_id in conversation_ids:
        await hub.broadcast_room(conversation_id, event)
    return presence


@router.get('/conversations/{conversation_id}/presence', response_model=list[PresenceRead])
def conversation_presence(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_member(db, conversation_id, user.id)
    member_ids = list(db.scalars(select(ConversationMember.user_id).where(ConversationMember.conversation_id == conversation_id, ConversationMember.status == 'active')).all())
    presences = {item.user_id: item for item in db.scalars(select(UserPresence).where(UserPresence.user_id.in_(member_ids))).all()}
    result = []
    for member_id in member_ids:
        presence = presences.get(member_id) or UserPresence(user_id=member_id, status='offline')
        data = PresenceRead.model_validate(presence, from_attributes=True).model_dump()
        if hub.is_user_online(member_id) and data['status'] == 'offline':
            data['status'] = 'online'
        result.append(data)
    return result


@router.get('/blocks', response_model=list[BlockRead])
def list_blocks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(UserBlock, User)
        .join(User, User.id == UserBlock.blocked_id)
        .where(UserBlock.blocker_id == user.id)
        .order_by(UserBlock.created_at.desc())
    ).all()
    return [
        {
            'id': block.id,
            'blocked_id': block.blocked_id,
            'username': blocked.username,
            'display_name': user_display_name(blocked),
            'created_at': block.created_at,
        }
        for block, blocked in rows
    ]


@router.post('/blocks/{blocked_user_id}', response_model=BlockRead, status_code=201)
def block_user(blocked_user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if blocked_user_id == user.id:
        raise HTTPException(status_code=422, detail='You cannot block yourself')
    blocked = db.get(User, blocked_user_id)
    if not blocked or blocked.status != 'active':
        raise HTTPException(status_code=404, detail='User not found')
    block = db.scalar(select(UserBlock).where(UserBlock.blocker_id == user.id, UserBlock.blocked_id == blocked_user_id))
    if not block:
        block = UserBlock(blocker_id=user.id, blocked_id=blocked_user_id)
        db.add(block)
        db.commit()
        db.refresh(block)
    return {'id': block.id, 'blocked_id': blocked.id, 'username': blocked.username, 'display_name': user_display_name(blocked), 'created_at': block.created_at}


@router.delete('/blocks/{blocked_user_id}', status_code=204)
def unblock_user(blocked_user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    block = db.scalar(select(UserBlock).where(UserBlock.blocker_id == user.id, UserBlock.blocked_id == blocked_user_id))
    if block:
        db.delete(block)
        db.commit()


@router.get('/notifications', response_model=PaginatedNotifications)
def list_notifications(
    unread_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=30, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    unread_count = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0
    items = list(db.scalars(stmt.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all())
    return {'items': items, 'total': total, 'unread_count': unread_count, 'page': page, 'page_size': page_size}


@router.post('/notifications/{notification_id}/read', response_model=NotificationRead)
def read_notification(notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    notification = db.get(Notification, notification_id)
    if not notification or notification.user_id != user.id:
        raise HTTPException(status_code=404, detail='Notification not found')
    notification.read_at = notification.read_at or utcnow()
    db.commit()
    db.refresh(notification)
    return notification


@router.post('/notifications/read-all', response_model=APIMessage)
def read_all_notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    notifications = list(db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))).all())
    now = utcnow()
    for notification in notifications:
        notification.read_at = now
    db.commit()
    return APIMessage(message=f'Marked {len(notifications)} notifications as read')


@router.get('/notification-preferences', response_model=NotificationPreferenceRead)
def get_notification_preferences(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    preference = db.get(NotificationPreference, user.id)
    if not preference:
        preference = NotificationPreference(user_id=user.id)
        db.add(preference)
        db.commit()
        db.refresh(preference)
    return preference


@router.patch('/notification-preferences', response_model=NotificationPreferenceRead)
def update_notification_preferences(payload: NotificationPreferenceUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    preference = db.get(NotificationPreference, user.id)
    if not preference:
        preference = NotificationPreference(user_id=user.id)
        db.add(preference)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(preference, field, value)
    preference.updated_at = utcnow()
    db.commit()
    db.refresh(preference)
    return preference
