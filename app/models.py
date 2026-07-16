from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid4_str() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = 'users'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default='pending_verification', index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    phone_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Permission(Base):
    __tablename__ = 'permissions'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    code: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Role(Base):
    __tablename__ = 'roles'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    scope: Mapped[str] = mapped_column(String(20), default='global')
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RolePermission(Base):
    __tablename__ = 'role_permissions'
    __table_args__ = (UniqueConstraint('role_id', 'permission_id', name='uq_role_permission'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    role_id: Mapped[str] = mapped_column(ForeignKey('roles.id', ondelete='CASCADE'), index=True)
    permission_id: Mapped[str] = mapped_column(ForeignKey('permissions.id', ondelete='CASCADE'), index=True)


class UserRole(Base):
    __tablename__ = 'user_roles'
    __table_args__ = (UniqueConstraint('user_id', 'role_id', name='uq_user_role'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey('roles.id', ondelete='CASCADE'), index=True)
    assigned_by: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Organization(Base):
    __tablename__ = 'organizations'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='active')
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Membership(Base):
    __tablename__ = 'memberships'
    __table_args__ = (UniqueConstraint('organization_id', 'user_id', name='uq_org_membership'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey('roles.id', ondelete='RESTRICT'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='active')
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Invitation(Base):
    __tablename__ = 'invitations'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    organization_id: Mapped[str] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    role_id: Mapped[str] = mapped_column(ForeignKey('roles.id', ondelete='RESTRICT'))
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    invited_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OneTimeToken(Base):
    __tablename__ = 'one_time_tokens'
    __table_args__ = (Index('ix_ott_user_purpose', 'user_id', 'purpose'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    purpose: Mapped[str] = mapped_column(String(40), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RefreshSession(Base):
    __tablename__ = 'refresh_sessions'
    __table_args__ = (Index('ix_refresh_family', 'family_id'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    jti_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('refresh_sessions.id', ondelete='SET NULL'), nullable=True)
    replaced_by_id: Mapped[str | None] = mapped_column(ForeignKey('refresh_sessions.id', ondelete='SET NULL'), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    device_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)


class MFAMethod(Base):
    __tablename__ = 'mfa_methods'
    __table_args__ = (UniqueConstraint('user_id', 'method', name='uq_user_mfa_method'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    method: Mapped[str] = mapped_column(String(20), default='totp')
    secret_encrypted: Mapped[str] = mapped_column(Text)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupCode(Base):
    __tablename__ = 'backup_codes'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    code_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApiKey(Base):
    __tablename__ = 'api_keys'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    prefix: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    secret_hash: Mapped[str] = mapped_column(String(128), unique=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    __table_args__ = (Index('ix_audit_action_created', 'action', 'created_at'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id', ondelete='SET NULL'), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(120), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default='success')
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

# ---------------------------------------------------------------------------
# Real-time chat domain
# ---------------------------------------------------------------------------

class Conversation(Base):
    __tablename__ = 'conversations'
    __table_args__ = (
        Index('ix_conversations_org_status_updated', 'organization_id', 'status', 'updated_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey('organizations.id', ondelete='CASCADE'), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(20), default='group', index=True)
    direct_key: Mapped[str | None] = mapped_column(String(80), nullable=True, unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(180), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    is_private: Mapped[bool] = mapped_column(Boolean, default=True)
    max_members: Mapped[int] = mapped_column(Integer, default=500)
    next_sequence: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationMember(Base):
    __tablename__ = 'conversation_members'
    __table_args__ = (
        UniqueConstraint('conversation_id', 'user_id', name='uq_conversation_member'),
        Index('ix_conversation_members_user_status', 'user_id', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20), default='member')
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    notification_level: Mapped[str] = mapped_column(String(20), default='all')
    last_read_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    muted_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invited_by: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)


class Message(Base):
    __tablename__ = 'messages'
    __table_args__ = (
        UniqueConstraint('conversation_id', 'sender_id', 'client_message_id', name='uq_message_client_id'),
        UniqueConstraint('conversation_id', 'sequence', name='uq_message_sequence'),
        Index('ix_messages_conversation_created', 'conversation_id', 'created_at'),
        Index('ix_messages_sender_created', 'sender_id', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    sender_id: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True, index=True)
    client_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('messages.id', ondelete='SET NULL'), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(20), default='text')
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='sent', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UploadAsset(Base):
    __tablename__ = 'upload_assets'
    __table_args__ = (Index('ix_upload_owner_status', 'owner_id', 'status'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(150))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    public_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageAttachment(Base):
    __tablename__ = 'message_attachments'
    __table_args__ = (UniqueConstraint('message_id', 'asset_id', name='uq_message_asset'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), index=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey('upload_assets.id', ondelete='RESTRICT'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageReaction(Base):
    __tablename__ = 'message_reactions'
    __table_args__ = (
        UniqueConstraint('message_id', 'user_id', 'emoji', name='uq_message_user_emoji'),
        Index('ix_reactions_message_created', 'message_id', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    emoji: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MessageReceipt(Base):
    __tablename__ = 'message_receipts'
    __table_args__ = (UniqueConstraint('message_id', 'user_id', name='uq_message_receipt'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PinnedMessage(Base):
    __tablename__ = 'pinned_messages'
    __table_args__ = (UniqueConstraint('conversation_id', 'message_id', name='uq_conversation_pin'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), index=True)
    pinned_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ConversationInvite(Base):
    __tablename__ = 'conversation_invites'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    conversation_id: Mapped[str] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'), index=True)
    token_hash: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'))
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserPresence(Base):
    __tablename__ = 'user_presence'

    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), default='offline', index=True)
    custom_status: Mapped[str | None] = mapped_column(String(160), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserBlock(Base):
    __tablename__ = 'user_blocks'
    __table_args__ = (UniqueConstraint('blocker_id', 'blocked_id', name='uq_user_block'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    blocker_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    blocked_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Notification(Base):
    __tablename__ = 'notifications'
    __table_args__ = (Index('ix_notifications_user_read_created', 'user_id', 'read_at', 'created_at'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    type: Mapped[str] = mapped_column(String(50), index=True)
    title: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(Text)
    data_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class NotificationPreference(Base):
    __tablename__ = 'notification_preferences'

    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    push_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    direct_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    group_messages: Mapped[bool] = mapped_column(Boolean, default=True)
    mentions_only: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class MessageReport(Base):
    __tablename__ = 'message_reports'
    __table_args__ = (
        UniqueConstraint('message_id', 'reporter_id', name='uq_message_reporter'),
        Index('ix_reports_status_created', 'status', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    message_id: Mapped[str] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), index=True)
    reporter_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    reason: Mapped[str] = mapped_column(String(50))
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='open', index=True)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EventOutbox(Base):
    __tablename__ = 'event_outbox'
    __table_args__ = (Index('ix_outbox_status_available', 'status', 'available_at'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    topic: Mapped[str] = mapped_column(String(120), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(80))
    aggregate_id: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

# ---------------------------------------------------------------------------
# Enterprise social media domain
# ---------------------------------------------------------------------------

class SocialProfile(Base):
    __tablename__ = 'social_profiles'

    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    country: Mapped[str | None] = mapped_column(String(120), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_visibility: Mapped[str] = mapped_column(String(20), default='public', index=True)
    message_permission: Mapped[str] = mapped_column(String(20), default='friends')
    friend_list_visibility: Mapped[str] = mapped_column(String(20), default='friends')
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    following_count: Mapped[int] = mapped_column(Integer, default=0)
    friend_count: Mapped[int] = mapped_column(Integer, default=0)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Follow(Base):
    __tablename__ = 'follows'
    __table_args__ = (
        UniqueConstraint('follower_id', 'following_id', name='uq_follow_edge'),
        Index('ix_follows_following_created', 'following_id', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    follower_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    following_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FriendRequest(Base):
    __tablename__ = 'friend_requests'
    __table_args__ = (
        UniqueConstraint('requester_id', 'addressee_id', name='uq_friend_request_direction'),
        Index('ix_friend_requests_addressee_status', 'addressee_id', 'status'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    requester_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    addressee_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Friendship(Base):
    __tablename__ = 'friendships'
    __table_args__ = (UniqueConstraint('user_low_id', 'user_high_id', name='uq_friendship_pair'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    user_low_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    user_high_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialPage(Base):
    __tablename__ = 'social_pages'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    category: Mapped[str] = mapped_column(String(100), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PageAdmin(Base):
    __tablename__ = 'page_admins'
    __table_args__ = (UniqueConstraint('page_id', 'user_id', name='uq_page_admin'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    page_id: Mapped[str] = mapped_column(ForeignKey('social_pages.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20), default='editor')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PageFollow(Base):
    __tablename__ = 'page_follows'
    __table_args__ = (UniqueConstraint('page_id', 'user_id', name='uq_page_follow'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    page_id: Mapped[str] = mapped_column(ForeignKey('social_pages.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialGroup(Base):
    __tablename__ = 'social_groups'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    privacy: Mapped[str] = mapped_column(String(20), default='public', index=True)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=False)
    avatar_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    member_count: Mapped[int] = mapped_column(Integer, default=1)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GroupMember(Base):
    __tablename__ = 'group_members'
    __table_args__ = (UniqueConstraint('group_id', 'user_id', name='uq_group_member'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    group_id: Mapped[str] = mapped_column(ForeignKey('social_groups.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    role: Mapped[str] = mapped_column(String(20), default='member')
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SocialPost(Base):
    __tablename__ = 'social_posts'
    __table_args__ = (
        Index('ix_social_posts_author_published', 'author_id', 'published_at'),
        Index('ix_social_posts_status_visibility', 'status', 'visibility'),
        Index('ix_social_posts_group_published', 'group_id', 'published_at'),
        Index('ix_social_posts_page_published', 'page_id', 'published_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    page_id: Mapped[str | None] = mapped_column(ForeignKey('social_pages.id', ondelete='CASCADE'), nullable=True, index=True)
    group_id: Mapped[str | None] = mapped_column(ForeignKey('social_groups.id', ondelete='CASCADE'), nullable=True, index=True)
    shared_post_id: Mapped[str | None] = mapped_column(ForeignKey('social_posts.id', ondelete='SET NULL'), nullable=True, index=True)
    type: Mapped[str] = mapped_column(String(20), default='text')
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), default='public', index=True)
    status: Mapped[str] = mapped_column(String(20), default='published', index=True)
    location: Mapped[str | None] = mapped_column(String(240), nullable=True)
    feeling: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reaction_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    share_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PostMedia(Base):
    __tablename__ = 'post_media'
    __table_args__ = (Index('ix_post_media_post_order', 'post_id', 'sort_order'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    post_id: Mapped[str] = mapped_column(ForeignKey('social_posts.id', ondelete='CASCADE'), index=True)
    media_type: Mapped[str] = mapped_column(String(20))
    url: Mapped[str] = mapped_column(String(1200))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostReaction(Base):
    __tablename__ = 'post_reactions'
    __table_args__ = (UniqueConstraint('post_id', 'user_id', name='uq_post_reaction_user'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    post_id: Mapped[str] = mapped_column(ForeignKey('social_posts.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    reaction: Mapped[str] = mapped_column(String(20), default='like', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostComment(Base):
    __tablename__ = 'post_comments'
    __table_args__ = (
        Index('ix_post_comments_post_created', 'post_id', 'created_at'),
        Index('ix_post_comments_parent_created', 'parent_id', 'created_at'),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    post_id: Mapped[str] = mapped_column(ForeignKey('social_posts.id', ondelete='CASCADE'), index=True)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey('post_comments.id', ondelete='CASCADE'), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    reaction_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommentReaction(Base):
    __tablename__ = 'comment_reactions'
    __table_args__ = (UniqueConstraint('comment_id', 'user_id', name='uq_comment_reaction_user'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    comment_id: Mapped[str] = mapped_column(ForeignKey('post_comments.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    reaction: Mapped[str] = mapped_column(String(20), default='like')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SavedPost(Base):
    __tablename__ = 'saved_posts'
    __table_args__ = (UniqueConstraint('post_id', 'user_id', name='uq_saved_post_user'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    post_id: Mapped[str] = mapped_column(ForeignKey('social_posts.id', ondelete='CASCADE'), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    collection: Mapped[str] = mapped_column(String(100), default='default')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Hashtag(Base):
    __tablename__ = 'hashtags'

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    post_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostHashtag(Base):
    __tablename__ = 'post_hashtags'
    __table_args__ = (UniqueConstraint('post_id', 'hashtag_id', name='uq_post_hashtag'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    post_id: Mapped[str] = mapped_column(ForeignKey('social_posts.id', ondelete='CASCADE'), index=True)
    hashtag_id: Mapped[str] = mapped_column(ForeignKey('hashtags.id', ondelete='CASCADE'), index=True)


class Story(Base):
    __tablename__ = 'stories'
    __table_args__ = (Index('ix_stories_author_expires', 'author_id', 'expires_at'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    author_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    media_type: Mapped[str] = mapped_column(String(20))
    media_url: Mapped[str] = mapped_column(String(1200))
    thumbnail_url: Mapped[str | None] = mapped_column(String(1200), nullable=True)
    caption: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    visibility: Mapped[str] = mapped_column(String(20), default='friends')
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StoryView(Base):
    __tablename__ = 'story_views'
    __table_args__ = (UniqueConstraint('story_id', 'viewer_id', name='uq_story_view'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    story_id: Mapped[str] = mapped_column(ForeignKey('stories.id', ondelete='CASCADE'), index=True)
    viewer_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentReport(Base):
    __tablename__ = 'content_reports'
    __table_args__ = (Index('ix_content_reports_status_created', 'status', 'created_at'),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    reporter_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    resource_type: Mapped[str] = mapped_column(String(20), index=True)
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    reason: Mapped[str] = mapped_column(String(40), index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default='open', index=True)
    resolved_by: Mapped[str | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
