from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.chat_schemas import ChatDashboard, MessageReportCreate, MessageReportRead, ModerationAction, ReportUpdate
from app.database import get_db
from app.dependencies import get_current_user, require_permissions
from app.models import (
    Conversation,
    ConversationMember,
    Message,
    MessageReport,
    UploadAsset,
    User,
    UserPresence,
    utcnow,
)
from app.realtime import hub
from app.schemas import APIMessage
from app.services.audit_service import write_audit
from app.services.chat_service import require_member

router = APIRouter(prefix='/chat', tags=['Moderation'])


def report_dict(db: Session, report: MessageReport) -> dict:
    message = db.get(Message, report.message_id)
    return {
        'id': report.id,
        'message_id': report.message_id,
        'conversation_id': message.conversation_id if message else 'deleted',
        'reporter_id': report.reporter_id,
        'reason': report.reason,
        'details': report.details,
        'status': report.status,
        'resolved_by': report.resolved_by,
        'resolution': report.resolution,
        'created_at': report.created_at,
        'resolved_at': report.resolved_at,
    }


@router.post('/messages/{message_id}/report', response_model=MessageReportRead, status_code=201)
def report_message(
    message_id: str,
    payload: MessageReportCreate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = db.get(Message, message_id)
    if not message:
        raise HTTPException(status_code=404, detail='Message not found')
    require_member(db, message.conversation_id, user.id)
    if message.sender_id == user.id:
        raise HTTPException(status_code=422, detail='You cannot report your own message')
    existing = db.scalar(select(MessageReport).where(MessageReport.message_id == message.id, MessageReport.reporter_id == user.id))
    if existing:
        return report_dict(db, existing)
    report = MessageReport(message_id=message.id, reporter_id=user.id, reason=payload.reason, details=payload.details)
    db.add(report)
    db.flush()
    write_audit(db, 'chat.message_reported', request, actor_user_id=user.id, resource_type='message_report', resource_id=report.id, metadata={'message_id': message.id, 'reason': payload.reason})
    db.commit()
    db.refresh(report)
    return report_dict(db, report)


@router.get('/moderation/reports', response_model=list[MessageReportRead])
def list_reports(
    status_filter: str | None = Query(default=None, alias='status', pattern='^(open|reviewing|resolved|dismissed)$'),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_permissions('chat.moderate')),
    db: Session = Depends(get_db),
):
    stmt = select(MessageReport)
    if status_filter:
        stmt = stmt.where(MessageReport.status == status_filter)
    reports = list(db.scalars(stmt.order_by(MessageReport.created_at.desc()).limit(limit)).all())
    return [report_dict(db, report) for report in reports]


@router.patch('/moderation/reports/{report_id}', response_model=MessageReportRead)
async def resolve_report(
    report_id: str,
    payload: ReportUpdate,
    request: Request,
    user: User = Depends(require_permissions('chat.moderate')),
    db: Session = Depends(get_db),
):
    report = db.get(MessageReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Report not found')
    report.status = payload.status
    report.resolution = payload.resolution
    if payload.status in {'resolved', 'dismissed'}:
        report.resolved_by = user.id
        report.resolved_at = utcnow()
    message = db.get(Message, report.message_id)
    if payload.delete_message and message and message.status != 'deleted':
        message.status = 'deleted'
        message.content = None
        message.deleted_at = utcnow()
    write_audit(db, 'chat.report_updated', request, actor_user_id=user.id, resource_type='message_report', resource_id=report.id, metadata=payload.model_dump())
    db.commit()
    if message and payload.delete_message:
        await hub.broadcast_room(message.conversation_id, {'type': 'message.deleted', 'conversation_id': message.conversation_id, 'data': {'message_id': message.id, 'moderated': True}})
    return report_dict(db, report)


@router.post('/moderation/conversations/{conversation_id}/members/{member_user_id}/mute', response_model=APIMessage)
async def mute_member(
    conversation_id: str,
    member_user_id: str,
    payload: ModerationAction,
    request: Request,
    user: User = Depends(require_permissions('chat.moderate')),
    db: Session = Depends(get_db),
):
    member = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_user_id))
    if not member:
        raise HTTPException(status_code=404, detail='Conversation member not found')
    member.muted_until = utcnow() + timedelta(minutes=payload.duration_minutes or 60)
    write_audit(db, 'chat.member_muted', request, actor_user_id=user.id, resource_type='conversation_member', resource_id=member.id, metadata={'reason': payload.reason, 'muted_until': member.muted_until.isoformat()})
    db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'moderation.member_muted', 'conversation_id': conversation_id, 'data': {'user_id': member_user_id, 'muted_until': member.muted_until.isoformat()}})
    return APIMessage(message='Member muted')


@router.delete('/moderation/conversations/{conversation_id}/members/{member_user_id}/mute', response_model=APIMessage)
async def unmute_member(
    conversation_id: str,
    member_user_id: str,
    request: Request,
    user: User = Depends(require_permissions('chat.moderate')),
    db: Session = Depends(get_db),
):
    member = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_user_id))
    if not member:
        raise HTTPException(status_code=404, detail='Conversation member not found')
    member.muted_until = None
    write_audit(db, 'chat.member_unmuted', request, actor_user_id=user.id, resource_type='conversation_member', resource_id=member.id)
    db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'moderation.member_unmuted', 'conversation_id': conversation_id, 'data': {'user_id': member_user_id}})
    return APIMessage(message='Member unmuted')


@router.post('/moderation/conversations/{conversation_id}/members/{member_user_id}/ban', response_model=APIMessage)
async def ban_member(
    conversation_id: str,
    member_user_id: str,
    payload: ModerationAction,
    request: Request,
    user: User = Depends(require_permissions('chat.moderate')),
    db: Session = Depends(get_db),
):
    member = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_user_id))
    if not member:
        raise HTTPException(status_code=404, detail='Conversation member not found')
    if member.role == 'owner':
        raise HTTPException(status_code=409, detail='Conversation owner cannot be banned')
    member.status = 'banned'
    member.left_at = utcnow()
    write_audit(db, 'chat.member_banned', request, actor_user_id=user.id, resource_type='conversation_member', resource_id=member.id, metadata={'reason': payload.reason})
    db.commit()
    await hub.broadcast_room(conversation_id, {'type': 'moderation.member_banned', 'conversation_id': conversation_id, 'data': {'user_id': member_user_id}})
    await hub.broadcast_user(member_user_id, {'type': 'conversation.banned', 'conversation_id': conversation_id, 'data': {'reason': payload.reason}})
    return APIMessage(message='Member banned')


@router.post('/moderation/conversations/{conversation_id}/members/{member_user_id}/unban', response_model=APIMessage)
async def unban_member(
    conversation_id: str,
    member_user_id: str,
    request: Request,
    user: User = Depends(require_permissions('chat.moderate')),
    db: Session = Depends(get_db),
):
    member = db.scalar(select(ConversationMember).where(ConversationMember.conversation_id == conversation_id, ConversationMember.user_id == member_user_id))
    if not member:
        raise HTTPException(status_code=404, detail='Conversation member not found')
    member.status = 'removed'
    member.left_at = utcnow()
    write_audit(db, 'chat.member_unbanned', request, actor_user_id=user.id, resource_type='conversation_member', resource_id=member.id)
    db.commit()
    return APIMessage(message='Member unbanned; they may be invited again')


@router.get('/admin/dashboard', response_model=ChatDashboard)
def chat_dashboard(user: User = Depends(require_permissions('chat.admin')), db: Session = Depends(get_db)):
    now = utcnow()
    return {
        'users_total': db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0,
        'online_users': db.scalar(select(func.count(UserPresence.user_id)).where(UserPresence.status.in_(['online', 'away', 'dnd']))) or 0,
        'conversations_total': db.scalar(select(func.count(Conversation.id)).where(Conversation.deleted_at.is_(None))) or 0,
        'direct_conversations': db.scalar(select(func.count(Conversation.id)).where(Conversation.type == 'direct', Conversation.deleted_at.is_(None))) or 0,
        'group_conversations': db.scalar(select(func.count(Conversation.id)).where(Conversation.type == 'group', Conversation.deleted_at.is_(None))) or 0,
        'channels': db.scalar(select(func.count(Conversation.id)).where(Conversation.type == 'channel', Conversation.deleted_at.is_(None))) or 0,
        'messages_total': db.scalar(select(func.count(Message.id))) or 0,
        'messages_last_24h': db.scalar(select(func.count(Message.id)).where(Message.created_at >= now - timedelta(hours=24))) or 0,
        'open_reports': db.scalar(select(func.count(MessageReport.id)).where(MessageReport.status.in_(['open', 'reviewing']))) or 0,
        'pending_uploads': db.scalar(select(func.count(UploadAsset.id)).where(UploadAsset.status == 'pending')) or 0,
    }
