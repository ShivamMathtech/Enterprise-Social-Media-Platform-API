from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable

from fastapi import HTTPException, status
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.chat_schemas import MessageCreate
from app.models import (
    Conversation,
    ConversationMember,
    EventOutbox,
    Message,
    MessageAttachment,
    MessageReaction,
    MessageReceipt,
    Notification,
    UploadAsset,
    User,
    UserBlock,
    utcnow,
)

MANAGER_ROLES = {'owner', 'admin', 'moderator'}
ADMIN_ROLES = {'owner', 'admin'}


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def user_display_name(user: User | None) -> str | None:
    if not user:
        return None
    return f'{user.first_name} {user.last_name}'.strip()


def active_member(db: Session, conversation_id: str, user_id: str) -> ConversationMember | None:
    return db.scalar(
        select(ConversationMember).where(
            ConversationMember.conversation_id == conversation_id,
            ConversationMember.user_id == user_id,
            ConversationMember.status == 'active',
        )
    )


def require_member(db: Session, conversation_id: str, user_id: str) -> ConversationMember:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.deleted_at is not None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Conversation not found')
    member = active_member(db, conversation_id, user_id)
    if not member:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Active conversation membership required')
    return member


def require_manager(db: Session, conversation_id: str, user_id: str, admin_only: bool = False) -> ConversationMember:
    member = require_member(db, conversation_id, user_id)
    allowed = ADMIN_ROLES if admin_only else MANAGER_ROLES
    if member.role not in allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Conversation management permission required')
    return member


def blocked_between(db: Session, first: str, second: str) -> bool:
    return db.scalar(
        select(func.count(UserBlock.id)).where(
            or_(
                and_(UserBlock.blocker_id == first, UserBlock.blocked_id == second),
                and_(UserBlock.blocker_id == second, UserBlock.blocked_id == first),
            )
        )
    ) > 0


def add_outbox(db: Session, topic: str, aggregate_type: str, aggregate_id: str, payload: dict) -> None:
    db.add(EventOutbox(topic=topic, aggregate_type=aggregate_type, aggregate_id=aggregate_id, payload_json=payload))


def create_notification(db: Session, user_id: str, type_: str, title: str, body: str, data: dict | None = None) -> Notification:
    notification = Notification(user_id=user_id, type=type_, title=title, body=body, data_json=data)
    db.add(notification)
    return notification


def attachment_dict(asset: UploadAsset) -> dict:
    return {
        'id': asset.id,
        'file_name': asset.file_name,
        'mime_type': asset.mime_type,
        'size_bytes': asset.size_bytes,
        'public_url': asset.public_url,
        'checksum_sha256': asset.checksum_sha256,
        'status': asset.status,
    }


def message_dict(db: Session, message: Message, viewer_id: str | None = None) -> dict:
    sender = db.get(User, message.sender_id) if message.sender_id else None
    attachment_assets = list(
        db.scalars(
            select(UploadAsset)
            .join(MessageAttachment, MessageAttachment.asset_id == UploadAsset.id)
            .where(MessageAttachment.message_id == message.id)
            .order_by(MessageAttachment.created_at)
        ).all()
    )
    reaction_rows = db.execute(
        select(MessageReaction.emoji, func.count(MessageReaction.id))
        .where(MessageReaction.message_id == message.id)
        .group_by(MessageReaction.emoji)
        .order_by(MessageReaction.emoji)
    ).all()
    viewer_reactions = set()
    if viewer_id:
        viewer_reactions = set(
            db.scalars(
                select(MessageReaction.emoji).where(
                    MessageReaction.message_id == message.id,
                    MessageReaction.user_id == viewer_id,
                )
            ).all()
        )
    return {
        'id': message.id,
        'conversation_id': message.conversation_id,
        'sender_id': message.sender_id,
        'sender_username': sender.username if sender else None,
        'sender_display_name': user_display_name(sender),
        'client_message_id': message.client_message_id,
        'parent_id': message.parent_id,
        'sequence': message.sequence,
        'type': message.type,
        'content': message.content if message.status != 'deleted' else None,
        'metadata': message.metadata_json,
        'status': message.status,
        'attachments': [attachment_dict(asset) for asset in attachment_assets],
        'reactions': [
            {'emoji': emoji, 'count': count, 'reacted_by_me': emoji in viewer_reactions}
            for emoji, count in reaction_rows
        ],
        'created_at': message.created_at,
        'edited_at': message.edited_at,
        'deleted_at': message.deleted_at,
    }


def conversation_dict(db: Session, conversation: Conversation, user_id: str) -> dict:
    member = require_member(db, conversation.id, user_id)
    member_count = db.scalar(
        select(func.count(ConversationMember.id)).where(
            ConversationMember.conversation_id == conversation.id,
            ConversationMember.status == 'active',
        )
    ) or 0
    last_message = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(desc(Message.sequence))
        .limit(1)
    )
    last_read_sequence = 0
    if member.last_read_message_id:
        read_message = db.get(Message, member.last_read_message_id)
        if read_message and read_message.conversation_id == conversation.id:
            last_read_sequence = read_message.sequence
    unread_count = db.scalar(
        select(func.count(Message.id)).where(
            Message.conversation_id == conversation.id,
            Message.sequence > last_read_sequence,
            Message.sender_id != user_id,
            Message.status != 'deleted',
        )
    ) or 0
    return {
        'id': conversation.id,
        'organization_id': conversation.organization_id,
        'type': conversation.type,
        'title': conversation.title,
        'description': conversation.description,
        'avatar_url': conversation.avatar_url,
        'created_by': conversation.created_by,
        'status': conversation.status,
        'is_private': conversation.is_private,
        'max_members': conversation.max_members,
        'member_count': member_count,
        'unread_count': unread_count,
        'my_role': member.role,
        'my_notification_level': member.notification_level,
        'last_message': message_dict(db, last_message, user_id) if last_message else None,
        'last_message_at': conversation.last_message_at,
        'created_at': conversation.created_at,
        'updated_at': conversation.updated_at,
    }


def member_dict(db: Session, member: ConversationMember) -> dict:
    user = db.get(User, member.user_id)
    return {
        'id': member.id,
        'conversation_id': member.conversation_id,
        'user_id': member.user_id,
        'username': user.username if user else 'deleted-user',
        'display_name': user_display_name(user) or 'Deleted User',
        'avatar_url': None,
        'role': member.role,
        'status': member.status,
        'notification_level': member.notification_level,
        'muted_until': member.muted_until,
        'last_read_message_id': member.last_read_message_id,
        'last_read_at': member.last_read_at,
        'joined_at': member.joined_at,
    }


def create_message(db: Session, conversation_id: str, sender: User, payload: MessageCreate) -> Message:
    member = require_member(db, conversation_id, sender.id)
    conversation = db.get(Conversation, conversation_id)
    assert conversation is not None
    if conversation.status != 'active':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Conversation is not active')
    muted_until = as_utc(member.muted_until)
    if muted_until and muted_until > utcnow():
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='You are muted in this conversation')

    if payload.client_message_id:
        existing = db.scalar(
            select(Message).where(
                Message.conversation_id == conversation_id,
                Message.sender_id == sender.id,
                Message.client_message_id == payload.client_message_id,
            )
        )
        if existing:
            setattr(existing, '_idempotent_replay', True)
            return existing

    if payload.parent_id:
        parent = db.get(Message, payload.parent_id)
        if not parent or parent.conversation_id != conversation_id:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='Reply parent is not in this conversation')

    assets: list[UploadAsset] = []
    if payload.attachment_ids:
        assets = list(db.scalars(select(UploadAsset).where(UploadAsset.id.in_(set(payload.attachment_ids)))).all())
        if len(assets) != len(set(payload.attachment_ids)):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='One or more attachments do not exist')
        for asset in assets:
            if asset.owner_id != sender.id or asset.status != 'ready':
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Attachments must be ready and owned by the sender')

    conversation.next_sequence += 1
    conversation.last_message_at = utcnow()
    conversation.updated_at = utcnow()
    message = Message(
        conversation_id=conversation_id,
        sender_id=sender.id,
        client_message_id=payload.client_message_id,
        parent_id=payload.parent_id,
        sequence=conversation.next_sequence,
        type=payload.type,
        content=payload.content.strip() if payload.content else None,
        metadata_json=payload.metadata,
    )
    db.add(message)
    db.flush()
    for asset in assets:
        db.add(MessageAttachment(message_id=message.id, asset_id=asset.id))

    # The sender has implicitly read their own new message.
    member.last_read_message_id = message.id
    member.last_read_at = utcnow()
    db.add(MessageReceipt(message_id=message.id, user_id=sender.id, delivered_at=utcnow(), read_at=utcnow()))

    # In-app notifications for members not currently muted. Direct chats notify all peers;
    # groups notify everyone unless configured for mentions only.
    mentioned_usernames = set(re.findall(r'@([A-Za-z0-9_.-]{3,64})', payload.content or ''))
    recipients = list(
        db.scalars(
            select(ConversationMember).where(
                ConversationMember.conversation_id == conversation_id,
                ConversationMember.status == 'active',
                ConversationMember.user_id != sender.id,
                ConversationMember.notification_level != 'none',
            )
        ).all()
    )
    for recipient_member in recipients:
        recipient = db.get(User, recipient_member.user_id)
        if not recipient:
            continue
        muted = as_utc(recipient_member.muted_until)
        if muted and muted > utcnow():
            continue
        if recipient_member.notification_level == 'mentions' and recipient.username not in mentioned_usernames:
            continue
        title = conversation.title or user_display_name(sender) or sender.username
        create_notification(
            db,
            recipient.id,
            'chat.message',
            title,
            (payload.content or f'Sent a {payload.type} attachment')[:240],
            {'conversation_id': conversation_id, 'message_id': message.id, 'sender_id': sender.id},
        )

    add_outbox(
        db,
        'chat.message.created',
        'message',
        message.id,
        {'conversation_id': conversation_id, 'message_id': message.id, 'sender_id': sender.id, 'sequence': message.sequence},
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if payload.client_message_id:
            existing = db.scalar(
                select(Message).where(
                    Message.conversation_id == conversation_id,
                    Message.sender_id == sender.id,
                    Message.client_message_id == payload.client_message_id,
                )
            )
            if existing:
                setattr(existing, '_idempotent_replay', True)
                return existing
        raise
    db.refresh(message)
    setattr(message, '_idempotent_replay', False)
    return message


def mark_read(db: Session, conversation_id: str, user_id: str, message_id: str) -> MessageReceipt:
    member = require_member(db, conversation_id, user_id)
    message = db.get(Message, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Message not found in this conversation')
    current_sequence = 0
    if member.last_read_message_id:
        current = db.get(Message, member.last_read_message_id)
        current_sequence = current.sequence if current and current.conversation_id == conversation_id else 0
    if message.sequence >= current_sequence:
        member.last_read_message_id = message.id
        member.last_read_at = utcnow()
    receipt = db.scalar(select(MessageReceipt).where(MessageReceipt.message_id == message.id, MessageReceipt.user_id == user_id))
    if not receipt:
        receipt = MessageReceipt(message_id=message.id, user_id=user_id)
        db.add(receipt)
    receipt.delivered_at = receipt.delivered_at or utcnow()
    receipt.read_at = utcnow()
    add_outbox(db, 'chat.message.read', 'message', message.id, {'conversation_id': conversation_id, 'message_id': message.id, 'user_id': user_id})
    db.commit()
    db.refresh(receipt)
    return receipt
