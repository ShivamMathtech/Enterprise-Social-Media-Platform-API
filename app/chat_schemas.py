from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int


class ConversationCreate(BaseModel):
    type: Literal['direct', 'group', 'channel'] = 'group'
    participant_ids: list[str] = Field(default_factory=list, max_length=500)
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=1000)
    organization_id: str | None = None
    is_private: bool = True
    max_members: int = Field(default=500, ge=2, le=5000)

    @model_validator(mode='after')
    def validate_type(self):
        unique = list(dict.fromkeys(self.participant_ids))
        self.participant_ids = unique
        if self.type == 'direct' and len(unique) != 1:
            raise ValueError('A direct conversation requires exactly one other participant')
        if self.type in {'group', 'channel'} and not self.title:
            raise ValueError('Group and channel conversations require a title')
        return self


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    description: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=1000)
    is_private: bool | None = None
    max_members: int | None = Field(default=None, ge=2, le=5000)


class ConversationRead(BaseModel):
    id: str
    organization_id: str | None
    type: str
    title: str | None
    description: str | None
    avatar_url: str | None
    created_by: str
    status: str
    is_private: bool
    max_members: int
    member_count: int
    unread_count: int
    my_role: str
    my_notification_level: str
    last_message: dict[str, Any] | None = None
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PaginatedConversations(BaseModel):
    items: list[ConversationRead]
    total: int
    page: int
    page_size: int


class MemberAdd(BaseModel):
    user_ids: list[str] = Field(min_length=1, max_length=500)
    role: Literal['admin', 'moderator', 'member'] = 'member'


class MemberUpdate(BaseModel):
    role: Literal['owner', 'admin', 'moderator', 'member'] | None = None
    notification_level: Literal['all', 'mentions', 'none'] | None = None
    status: Literal['active', 'removed', 'banned'] | None = None


class ConversationMemberRead(BaseModel):
    id: str
    conversation_id: str
    user_id: str
    username: str
    display_name: str
    avatar_url: str | None = None
    role: str
    status: str
    notification_level: str
    muted_until: datetime | None
    last_read_message_id: str | None
    last_read_at: datetime | None
    joined_at: datetime


class MessageCreate(BaseModel):
    content: str | None = Field(default=None, max_length=10000)
    type: Literal['text', 'image', 'file', 'audio', 'video', 'system'] = 'text'
    client_message_id: str | None = Field(default=None, min_length=1, max_length=100)
    parent_id: str | None = None
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)
    metadata: dict[str, Any] | None = None

    @model_validator(mode='after')
    def validate_content(self):
        if self.type == 'text' and not (self.content and self.content.strip()):
            raise ValueError('Text messages require non-empty content')
        if not self.content and not self.attachment_ids and self.type != 'system':
            raise ValueError('A message requires content or an attachment')
        return self


class MessageUpdate(BaseModel):
    content: str = Field(min_length=1, max_length=10000)


class AttachmentRead(BaseModel):
    id: str
    file_name: str
    mime_type: str
    size_bytes: int
    public_url: str | None
    checksum_sha256: str | None
    status: str


class ReactionSummary(BaseModel):
    emoji: str
    count: int
    reacted_by_me: bool


class MessageRead(BaseModel):
    id: str
    conversation_id: str
    sender_id: str | None
    sender_username: str | None
    sender_display_name: str | None
    client_message_id: str | None
    parent_id: str | None
    sequence: int
    type: str
    content: str | None
    metadata: dict[str, Any] | None
    status: str
    attachments: list[AttachmentRead] = []
    reactions: list[ReactionSummary] = []
    created_at: datetime
    edited_at: datetime | None
    deleted_at: datetime | None


class PaginatedMessages(BaseModel):
    items: list[MessageRead]
    has_more: bool
    next_before_sequence: int | None = None


class ReactionRequest(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ReadReceiptRequest(BaseModel):
    message_id: str


class ReceiptRead(BaseModel):
    user_id: str
    username: str
    delivered_at: datetime | None
    read_at: datetime | None


class UploadCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=150)
    size_bytes: int = Field(gt=0, le=25 * 1024 * 1024)
    checksum_sha256: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{64}$')


class UploadTicket(BaseModel):
    id: str
    storage_key: str
    upload_method: str = 'PUT'
    upload_url: str
    expires_in_seconds: int = 900
    max_size_bytes: int


class UploadComplete(BaseModel):
    checksum_sha256: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{64}$')


class InviteCreate(BaseModel):
    expires_in_hours: int | None = Field(default=72, ge=1, le=720)
    max_uses: int | None = Field(default=None, ge=1, le=10000)


class InviteRead(BaseModel):
    id: str
    conversation_id: str
    max_uses: int | None
    uses: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    invite_token: str | None = None


class InviteAccept(BaseModel):
    token: str = Field(min_length=16)


class PresenceUpdate(BaseModel):
    status: Literal['online', 'away', 'dnd', 'offline']
    custom_status: str | None = Field(default=None, max_length=160)


class PresenceRead(BaseModel):
    user_id: str
    status: str
    custom_status: str | None
    last_seen_at: datetime
    updated_at: datetime


class BlockRead(BaseModel):
    id: str
    blocked_id: str
    username: str
    display_name: str
    created_at: datetime


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    type: str
    title: str
    body: str
    data_json: dict | None
    read_at: datetime | None
    created_at: datetime


class PaginatedNotifications(BaseModel):
    items: list[NotificationRead]
    total: int
    unread_count: int
    page: int
    page_size: int


class NotificationPreferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    in_app_enabled: bool
    email_enabled: bool
    push_enabled: bool
    direct_messages: bool
    group_messages: bool
    mentions_only: bool
    updated_at: datetime


class NotificationPreferenceUpdate(BaseModel):
    in_app_enabled: bool | None = None
    email_enabled: bool | None = None
    push_enabled: bool | None = None
    direct_messages: bool | None = None
    group_messages: bool | None = None
    mentions_only: bool | None = None


class MessageReportCreate(BaseModel):
    reason: Literal['spam', 'harassment', 'hate', 'violence', 'sexual_content', 'illegal_content', 'privacy', 'other']
    details: str | None = Field(default=None, max_length=2000)


class ReportUpdate(BaseModel):
    status: Literal['reviewing', 'resolved', 'dismissed']
    resolution: str | None = Field(default=None, max_length=2000)
    delete_message: bool = False


class MessageReportRead(BaseModel):
    id: str
    message_id: str
    conversation_id: str
    reporter_id: str
    reason: str
    details: str | None
    status: str
    resolved_by: str | None
    resolution: str | None
    created_at: datetime
    resolved_at: datetime | None


class ModerationAction(BaseModel):
    duration_minutes: int | None = Field(default=None, ge=1, le=525600)
    reason: str | None = Field(default=None, max_length=500)


class ChatDashboard(BaseModel):
    users_total: int
    online_users: int
    conversations_total: int
    direct_conversations: int
    group_conversations: int
    channels: int
    messages_total: int
    messages_last_24h: int
    open_reports: int
    pending_uploads: int


class WebSocketEvent(BaseModel):
    type: str
    request_id: str | None = None
    conversation_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
