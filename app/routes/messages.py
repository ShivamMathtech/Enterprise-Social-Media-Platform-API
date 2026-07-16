from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from app.chat_schemas import (
    AttachmentRead,
    MessageCreate,
    MessageRead,
    MessageUpdate,
    PaginatedMessages,
    ReactionRequest,
    ReadReceiptRequest,
    ReceiptRead,
    UploadComplete,
    UploadCreate,
    UploadTicket,
)
from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageAttachment,
    MessageReaction,
    MessageReceipt,
    PinnedMessage,
    UploadAsset,
    User,
    utcnow,
)
from app.realtime import hub
from app.schemas import APIMessage
from app.security import secure_token
from app.services.audit_service import write_audit
from app.services.chat_service import (
    MANAGER_ROLES,
    create_message,
    mark_read,
    message_dict,
    require_manager,
    require_member,
)

router = APIRouter(prefix='/chat', tags=['Messages'])
settings = get_settings()


def _get_visible_message(db: Session, message_id: str, user_id: str) -> Message:
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail='Message not found')
    require_member(db, message.conversation_id, user_id)
    return message


@router.post('/conversations/{conversation_id}/messages', response_model=MessageRead, status_code=201)
async def send_message(
    conversation_id: str,
    payload: MessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = create_message(db, conversation_id, user, payload)
    data = message_dict(db, message, user.id)
    if not getattr(message, '_idempotent_replay', False):
        write_audit(db, 'chat.message_sent', request, actor_user_id=user.id, resource_type='message', resource_id=message.id, metadata={'conversation_id': conversation_id})
        db.commit()
        await hub.broadcast_room(conversation_id, {'type': 'message.created', 'conversation_id': conversation_id, 'data': data})
    return data


@router.get('/conversations/{conversation_id}/messages', response_model=PaginatedMessages)
def list_messages(
    conversation_id: str,
    before_sequence: int | None = Query(default=None, ge=1),
    limit: int = Query(default=50, ge=1, le=100),
    include_deleted: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_member(db, conversation_id, user.id)
    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if before_sequence:
        stmt = stmt.where(Message.sequence < before_sequence)
    if not include_deleted:
        stmt = stmt.where(Message.status != 'deleted')
    messages = list(db.scalars(stmt.order_by(desc(Message.sequence)).limit(limit + 1)).all())
    has_more = len(messages) > limit
    messages = messages[:limit]
    next_before = messages[-1].sequence if has_more and messages else None
    messages.reverse()
    return {'items': [message_dict(db, message, user.id) for message in messages], 'has_more': has_more, 'next_before_sequence': next_before}


@router.get('/messages/{message_id}', response_model=MessageRead)
def get_message(message_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = _get_visible_message(db, message_id, user.id)
    return message_dict(db, message, user.id)


@router.patch('/messages/{message_id}', response_model=MessageRead)
async def edit_message(
    message_id: str,
    payload: MessageUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _get_visible_message(db, message_id, user.id)
    member = require_member(db, message.conversation_id, user.id)
    is_manager = member.role in MANAGER_ROLES
    if message.sender_id != user.id and not is_manager:
        raise HTTPException(status_code=403, detail='Only the sender or a conversation moderator can edit this message')
    if message.status == 'deleted':
        raise HTTPException(status_code=409, detail='Deleted messages cannot be edited')
    created_at = message.created_at.replace(tzinfo=message.created_at.tzinfo or utcnow().tzinfo)
    if message.sender_id == user.id and not is_manager and utcnow() > created_at + timedelta(minutes=settings.message_edit_window_minutes):
        raise HTTPException(status_code=409, detail='Message edit window has expired')
    message.content = payload.content.strip()
    message.status = 'edited'
    message.edited_at = utcnow()
    write_audit(db, 'chat.message_edited', request, actor_user_id=user.id, resource_type='message', resource_id=message.id)
    db.commit()
    data = message_dict(db, message, user.id)
    await hub.broadcast_room(message.conversation_id, {'type': 'message.updated', 'conversation_id': message.conversation_id, 'data': data})
    return data


@router.delete('/messages/{message_id}', response_model=APIMessage)
async def delete_message(
    message_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _get_visible_message(db, message_id, user.id)
    member = require_member(db, message.conversation_id, user.id)
    if message.sender_id != user.id and member.role not in MANAGER_ROLES:
        raise HTTPException(status_code=403, detail='Only the sender or a conversation moderator can delete this message')
    if message.status != 'deleted':
        message.status = 'deleted'
        message.content = None
        message.deleted_at = utcnow()
        write_audit(db, 'chat.message_deleted', request, actor_user_id=user.id, resource_type='message', resource_id=message.id)
        db.commit()
    await hub.broadcast_room(message.conversation_id, {'type': 'message.deleted', 'conversation_id': message.conversation_id, 'data': {'message_id': message.id, 'deleted_by': user.id}})
    return APIMessage(message='Message deleted')


@router.post('/messages/{message_id}/reactions', response_model=MessageRead)
async def add_reaction(message_id: str, payload: ReactionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = _get_visible_message(db, message_id, user.id)
    existing = db.scalar(select(MessageReaction).where(MessageReaction.message_id == message.id, MessageReaction.user_id == user.id, MessageReaction.emoji == payload.emoji))
    if not existing:
        db.add(MessageReaction(message_id=message.id, user_id=user.id, emoji=payload.emoji))
        db.commit()
    data = message_dict(db, message, user.id)
    await hub.broadcast_room(message.conversation_id, {'type': 'message.reaction_added', 'conversation_id': message.conversation_id, 'data': {'message_id': message.id, 'user_id': user.id, 'emoji': payload.emoji}})
    return data


@router.delete('/messages/{message_id}/reactions/{emoji}', response_model=MessageRead)
async def remove_reaction(message_id: str, emoji: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = _get_visible_message(db, message_id, user.id)
    reaction = db.scalar(select(MessageReaction).where(MessageReaction.message_id == message.id, MessageReaction.user_id == user.id, MessageReaction.emoji == emoji))
    if reaction:
        db.delete(reaction)
        db.commit()
    await hub.broadcast_room(message.conversation_id, {'type': 'message.reaction_removed', 'conversation_id': message.conversation_id, 'data': {'message_id': message.id, 'user_id': user.id, 'emoji': emoji}})
    return message_dict(db, message, user.id)


@router.post('/conversations/{conversation_id}/read', response_model=APIMessage)
async def read_conversation(
    conversation_id: str,
    payload: ReadReceiptRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    receipt = mark_read(db, conversation_id, user.id, payload.message_id)
    await hub.broadcast_room(conversation_id, {'type': 'message.read', 'conversation_id': conversation_id, 'data': {'message_id': payload.message_id, 'user_id': user.id, 'read_at': receipt.read_at.isoformat() if receipt.read_at else None}})
    return APIMessage(message='Read position updated')


@router.get('/messages/{message_id}/receipts', response_model=list[ReceiptRead])
def list_receipts(message_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    message = _get_visible_message(db, message_id, user.id)
    rows = db.execute(
        select(MessageReceipt, User)
        .join(User, User.id == MessageReceipt.user_id)
        .where(MessageReceipt.message_id == message.id)
        .order_by(MessageReceipt.read_at.desc().nullslast())
    ).all()
    return [{'user_id': receipt.user_id, 'username': receipt_user.username, 'delivered_at': receipt.delivered_at, 'read_at': receipt.read_at} for receipt, receipt_user in rows]


@router.post('/conversations/{conversation_id}/pins/{message_id}', response_model=MessageRead)
async def pin_message(conversation_id: str, message_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id)
    message = db.get(Message, message_id)
    if not message or message.conversation_id != conversation_id:
        raise HTTPException(status_code=404, detail='Message not found in this conversation')
    pin = db.scalar(select(PinnedMessage).where(PinnedMessage.conversation_id == conversation_id, PinnedMessage.message_id == message_id))
    if not pin:
        db.add(PinnedMessage(conversation_id=conversation_id, message_id=message_id, pinned_by=user.id))
        write_audit(db, 'chat.message_pinned', request, actor_user_id=user.id, resource_type='message', resource_id=message.id)
        db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'message.pinned', 'conversation_id': conversation_id, 'data': {'message_id': message_id, 'pinned_by': user.id}})
    return message_dict(db, message, user.id)


@router.delete('/conversations/{conversation_id}/pins/{message_id}', status_code=204)
async def unpin_message(conversation_id: str, message_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_manager(db, conversation_id, user.id)
    pin = db.scalar(select(PinnedMessage).where(PinnedMessage.conversation_id == conversation_id, PinnedMessage.message_id == message_id))
    if pin:
        db.delete(pin)
        write_audit(db, 'chat.message_unpinned', request, actor_user_id=user.id, resource_type='message', resource_id=message_id)
        db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'message.unpinned', 'conversation_id': conversation_id, 'data': {'message_id': message_id}})


@router.get('/conversations/{conversation_id}/pins', response_model=list[MessageRead])
def list_pins(conversation_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_member(db, conversation_id, user.id)
    messages = list(
        db.scalars(
            select(Message)
            .join(PinnedMessage, PinnedMessage.message_id == Message.id)
            .where(PinnedMessage.conversation_id == conversation_id)
            .order_by(PinnedMessage.pinned_at.desc())
        ).all()
    )
    return [message_dict(db, message, user.id) for message in messages]


@router.get('/search/messages', response_model=PaginatedMessages)
def search_messages(
    q: str = Query(min_length=2, max_length=200),
    conversation_id: str | None = None,
    sender_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member_conversations = select(ConversationMember.conversation_id).where(ConversationMember.user_id == user.id, ConversationMember.status == 'active')
    stmt = select(Message).where(Message.conversation_id.in_(member_conversations), Message.status != 'deleted', Message.content.ilike(f'%{q}%'))
    if conversation_id:
        require_member(db, conversation_id, user.id)
        stmt = stmt.where(Message.conversation_id == conversation_id)
    if sender_id:
        stmt = stmt.where(Message.sender_id == sender_id)
    messages = list(db.scalars(stmt.order_by(Message.created_at.desc()).limit(limit)).all())
    return {'items': [message_dict(db, message, user.id) for message in messages], 'has_more': False, 'next_before_sequence': None}


@router.post('/uploads/presign', response_model=UploadTicket, status_code=201)
def create_upload_ticket(payload: UploadCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if payload.size_bytes > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail='File exceeds configured upload limit')
    safe_name = Path(payload.file_name).name.replace(' ', '_')
    storage_key = f'{user.id}/{secure_token(12)}-{safe_name}'
    asset = UploadAsset(
        owner_id=user.id,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
        size_bytes=payload.size_bytes,
        storage_key=storage_key,
        checksum_sha256=payload.checksum_sha256,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)
    return {
        'id': asset.id,
        'storage_key': asset.storage_key,
        'upload_url': f'/api/v1/chat/uploads/{asset.id}/content',
        'max_size_bytes': settings.max_upload_bytes,
    }


@router.put('/uploads/{asset_id}/content', response_model=AttachmentRead)
async def upload_content(asset_id: str, file: UploadFile = File(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(UploadAsset, asset_id)
    if not asset or asset.owner_id != user.id:
        raise HTTPException(status_code=404, detail='Upload ticket not found')
    if asset.status == 'ready':
        return {
            'id': asset.id, 'file_name': asset.file_name, 'mime_type': asset.mime_type, 'size_bytes': asset.size_bytes,
            'public_url': asset.public_url, 'checksum_sha256': asset.checksum_sha256, 'status': asset.status,
        }
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes or len(data) != asset.size_bytes:
        raise HTTPException(status_code=422, detail='Uploaded file size does not match the upload ticket')
    digest = hashlib.sha256(data).hexdigest()
    if asset.checksum_sha256 and digest.lower() != asset.checksum_sha256.lower():
        raise HTTPException(status_code=422, detail='File checksum does not match')
    destination = Path(settings.upload_dir) / asset.storage_key
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    asset.checksum_sha256 = digest
    asset.mime_type = file.content_type or asset.mime_type
    asset.public_url = f"{settings.public_media_base_url.rstrip('/')}/{asset.storage_key}"
    asset.status = 'ready'
    asset.completed_at = utcnow()
    db.commit()
    return {
        'id': asset.id, 'file_name': asset.file_name, 'mime_type': asset.mime_type, 'size_bytes': asset.size_bytes,
        'public_url': asset.public_url, 'checksum_sha256': asset.checksum_sha256, 'status': asset.status,
    }


@router.post('/uploads/{asset_id}/complete', response_model=AttachmentRead)
def complete_external_upload(asset_id: str, payload: UploadComplete, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(UploadAsset, asset_id)
    if not asset or asset.owner_id != user.id:
        raise HTTPException(status_code=404, detail='Upload ticket not found')
    asset.checksum_sha256 = payload.checksum_sha256 or asset.checksum_sha256
    asset.public_url = asset.public_url or f"{settings.public_media_base_url.rstrip('/')}/{asset.storage_key}"
    asset.status = 'ready'
    asset.completed_at = utcnow()
    db.commit()
    return {
        'id': asset.id, 'file_name': asset.file_name, 'mime_type': asset.mime_type, 'size_bytes': asset.size_bytes,
        'public_url': asset.public_url, 'checksum_sha256': asset.checksum_sha256, 'status': asset.status,
    }


@router.delete('/uploads/{asset_id}', status_code=204)
def delete_upload(asset_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    asset = db.get(UploadAsset, asset_id)
    if not asset or asset.owner_id != user.id:
        raise HTTPException(status_code=404, detail='Upload not found')
    attached = db.scalar(select(func.count(MessageAttachment.id)).where(MessageAttachment.asset_id == asset.id)) or 0
    if attached:
        raise HTTPException(status_code=409, detail='Attached files cannot be deleted directly')
    file_path = Path(settings.upload_dir) / asset.storage_key
    if file_path.exists():
        file_path.unlink()
    db.delete(asset)
    db.commit()
