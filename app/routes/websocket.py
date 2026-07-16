from __future__ import annotations

import json
import uuid

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError
from sqlalchemy import select

from app.chat_schemas import MessageCreate, PresenceUpdate, ReactionRequest, WebSocketEvent
from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    ConversationMember,
    Message,
    MessageReaction,
    User,
    UserPresence,
    utcnow,
)
from app.realtime import hub
from app.security import decode_jwt
from app.services.chat_service import create_message, mark_read, message_dict, require_member

router = APIRouter(tags=['Realtime WebSocket'])
settings = get_settings()


async def send_error(websocket: WebSocket, code: str, message: str, request_id: str | None = None) -> None:
    await hub.send(websocket, {'type': 'error', 'request_id': request_id, 'data': {'code': code, 'message': message}})


@router.websocket('/ws')
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get('token')
    if not token:
        authorization = websocket.headers.get('authorization', '')
        token = authorization.removeprefix('Bearer ').strip() if authorization.startswith('Bearer ') else None
    if not token:
        await websocket.close(code=4401, reason='Access token required')
        return

    with SessionLocal() as db:
        try:
            payload = decode_jwt(token, expected_type='access')
        except jwt.PyJWTError:
            await websocket.close(code=4401, reason='Invalid or expired access token')
            return
        user = db.get(User, payload.get('sub'))
        if not user or user.status != 'active' or user.deleted_at is not None:
            await websocket.close(code=4403, reason='Account is not active')
            return
        room_ids = list(
            db.scalars(
                select(ConversationMember.conversation_id).where(
                    ConversationMember.user_id == user.id,
                    ConversationMember.status == 'active',
                )
            ).all()
        )
        user_id = user.id

    await hub.connect(websocket, user_id, room_ids)
    with SessionLocal() as db:
        presence = db.get(UserPresence, user_id)
        if not presence:
            presence = UserPresence(user_id=user_id)
            db.add(presence)
        presence.status = 'online'
        presence.last_seen_at = utcnow()
        presence.updated_at = utcnow()
        db.commit()
    connected_event = {
        'type': 'connection.ready',
        'data': {
            'user_id': user_id,
            'connection_id': str(uuid.uuid4()),
            'subscribed_conversation_ids': room_ids,
            'heartbeat_seconds': settings.websocket_heartbeat_seconds,
            'supported_events': [
                'conversation.subscribe', 'conversation.unsubscribe', 'message.send',
                'typing.start', 'typing.stop', 'receipt.read', 'reaction.add',
                'reaction.remove', 'presence.update', 'ping',
            ],
        },
    }
    await hub.send(websocket, connected_event)
    presence_event = {'type': 'presence.updated', 'data': {'user_id': user_id, 'status': 'online', 'custom_status': None, 'last_seen_at': utcnow()}}
    for room_id in room_ids:
        await hub.broadcast_room(room_id, presence_event, exclude=websocket)

    try:
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode('utf-8')) > settings.websocket_max_message_bytes:
                await send_error(websocket, 'payload_too_large', 'WebSocket event exceeds the configured size limit')
                continue
            try:
                parsed = json.loads(raw)
                event = WebSocketEvent.model_validate(parsed)
            except (json.JSONDecodeError, ValidationError) as exc:
                await send_error(websocket, 'invalid_event', 'Event must be valid JSON matching the event envelope')
                continue

            request_id = event.request_id
            try:
                with SessionLocal() as db:
                    user = db.get(User, user_id)
                    if not user or user.status != 'active':
                        await websocket.close(code=4403, reason='Account is no longer active')
                        return

                    if event.type == 'ping':
                        await hub.send(websocket, {'type': 'pong', 'request_id': request_id, 'data': {'server_time': utcnow()}})

                    elif event.type == 'conversation.subscribe':
                        conversation_id = event.conversation_id or event.data.get('conversation_id')
                        if not conversation_id:
                            raise ValueError('conversation_id is required')
                        require_member(db, conversation_id, user_id)
                        await hub.subscribe(websocket, conversation_id)
                        await hub.send(websocket, {'type': 'conversation.subscribed', 'request_id': request_id, 'conversation_id': conversation_id, 'data': {}})

                    elif event.type == 'conversation.unsubscribe':
                        conversation_id = event.conversation_id or event.data.get('conversation_id')
                        if not conversation_id:
                            raise ValueError('conversation_id is required')
                        await hub.unsubscribe(websocket, conversation_id)
                        await hub.send(websocket, {'type': 'conversation.unsubscribed', 'request_id': request_id, 'conversation_id': conversation_id, 'data': {}})

                    elif event.type == 'message.send':
                        conversation_id = event.conversation_id or event.data.pop('conversation_id', None)
                        if not conversation_id:
                            raise ValueError('conversation_id is required')
                        message_payload = MessageCreate.model_validate(event.data)
                        message = create_message(db, conversation_id, user, message_payload)
                        data = message_dict(db, message, user_id)
                        if getattr(message, '_idempotent_replay', False):
                            await hub.send(websocket, {'type': 'message.acknowledged', 'request_id': request_id, 'conversation_id': conversation_id, 'data': data})
                        else:
                            await hub.broadcast_room(conversation_id, {'type': 'message.created', 'request_id': request_id, 'conversation_id': conversation_id, 'data': data})

                    elif event.type in {'typing.start', 'typing.stop'}:
                        conversation_id = event.conversation_id or event.data.get('conversation_id')
                        if not conversation_id:
                            raise ValueError('conversation_id is required')
                        require_member(db, conversation_id, user_id)
                        await hub.broadcast_room(
                            conversation_id,
                            {
                                'type': event.type,
                                'request_id': request_id,
                                'conversation_id': conversation_id,
                                'data': {'user_id': user_id, 'expires_in_seconds': 8 if event.type == 'typing.start' else 0},
                            },
                            exclude=websocket,
                        )

                    elif event.type == 'receipt.read':
                        conversation_id = event.conversation_id or event.data.get('conversation_id')
                        message_id = event.data.get('message_id')
                        if not conversation_id or not message_id:
                            raise ValueError('conversation_id and message_id are required')
                        receipt = mark_read(db, conversation_id, user_id, message_id)
                        await hub.broadcast_room(conversation_id, {'type': 'message.read', 'request_id': request_id, 'conversation_id': conversation_id, 'data': {'message_id': message_id, 'user_id': user_id, 'read_at': receipt.read_at}})

                    elif event.type == 'reaction.add':
                        message_id = event.data.get('message_id')
                        reaction_payload = ReactionRequest.model_validate({'emoji': event.data.get('emoji')})
                        message = db.get(Message, message_id)
                        if not message:
                            raise ValueError('Message not found')
                        require_member(db, message.conversation_id, user_id)
                        existing = db.scalar(select(MessageReaction).where(MessageReaction.message_id == message.id, MessageReaction.user_id == user_id, MessageReaction.emoji == reaction_payload.emoji))
                        if not existing:
                            db.add(MessageReaction(message_id=message.id, user_id=user_id, emoji=reaction_payload.emoji))
                            db.commit()
                        await hub.broadcast_room(message.conversation_id, {'type': 'message.reaction_added', 'request_id': request_id, 'conversation_id': message.conversation_id, 'data': {'message_id': message.id, 'user_id': user_id, 'emoji': reaction_payload.emoji}})

                    elif event.type == 'reaction.remove':
                        message_id = event.data.get('message_id')
                        emoji = event.data.get('emoji')
                        message = db.get(Message, message_id)
                        if not message or not emoji:
                            raise ValueError('message_id and emoji are required')
                        require_member(db, message.conversation_id, user_id)
                        reaction = db.scalar(select(MessageReaction).where(MessageReaction.message_id == message.id, MessageReaction.user_id == user_id, MessageReaction.emoji == emoji))
                        if reaction:
                            db.delete(reaction)
                            db.commit()
                        await hub.broadcast_room(message.conversation_id, {'type': 'message.reaction_removed', 'request_id': request_id, 'conversation_id': message.conversation_id, 'data': {'message_id': message.id, 'user_id': user_id, 'emoji': emoji}})

                    elif event.type == 'presence.update':
                        presence_payload = PresenceUpdate.model_validate(event.data)
                        presence = db.get(UserPresence, user_id)
                        if not presence:
                            presence = UserPresence(user_id=user_id)
                            db.add(presence)
                        presence.status = presence_payload.status
                        presence.custom_status = presence_payload.custom_status
                        presence.last_seen_at = utcnow()
                        presence.updated_at = utcnow()
                        db.commit()
                        outgoing = {'type': 'presence.updated', 'request_id': request_id, 'data': {'user_id': user_id, 'status': presence.status, 'custom_status': presence.custom_status, 'last_seen_at': presence.last_seen_at}}
                        for room_id in room_ids:
                            await hub.broadcast_room(room_id, outgoing)

                    else:
                        await send_error(websocket, 'unsupported_event', f'Unsupported event type: {event.type}', request_id)

            except (ValueError, ValidationError) as exc:
                await send_error(websocket, 'invalid_event_data', str(exc), request_id)
            except Exception as exc:
                message = getattr(exc, 'detail', str(exc) or 'Event processing failed')
                await send_error(websocket, 'event_processing_failed', str(message), request_id)

    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(websocket)
        if not hub.is_user_online(user_id):
            with SessionLocal() as db:
                presence = db.get(UserPresence, user_id)
                if not presence:
                    presence = UserPresence(user_id=user_id)
                    db.add(presence)
                presence.status = 'offline'
                presence.last_seen_at = utcnow()
                presence.updated_at = utcnow()
                db.commit()
            event = {'type': 'presence.updated', 'data': {'user_id': user_id, 'status': 'offline', 'last_seen_at': utcnow()}}
            for room_id in room_ids:
                await hub.broadcast_room(room_id, event)
