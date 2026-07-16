from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


Visibility = Literal['public', 'friends', 'followers', 'private']
ReactionType = Literal['like', 'love', 'care', 'haha', 'wow', 'sad', 'angry']


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=160)
    bio: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=1000)
    cover_url: str | None = Field(default=None, max_length=1000)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    website: str | None = Field(default=None, max_length=500)
    profile_visibility: Visibility | None = None
    message_permission: Literal['everyone', 'friends', 'none'] | None = None
    friend_list_visibility: Literal['public', 'friends', 'private'] | None = None


class ProfileRead(BaseModel):
    user_id: str
    username: str
    display_name: str
    bio: str | None
    avatar_url: str | None
    cover_url: str | None
    city: str | None
    country: str | None
    website: str | None
    profile_visibility: str
    message_permission: str
    friend_list_visibility: str
    is_verified: bool
    follower_count: int
    following_count: int
    friend_count: int
    post_count: int
    is_following: bool = False
    is_friend: bool = False
    friendship_status: str | None = None
    created_at: datetime
    updated_at: datetime


class RelationshipRead(BaseModel):
    user_id: str
    username: str
    display_name: str
    avatar_url: str | None
    is_verified: bool = False
    followed_at: datetime | None = None
    friended_at: datetime | None = None


class FriendRequestRead(BaseModel):
    id: str
    requester_id: str
    addressee_id: str
    status: str
    requester_username: str | None = None
    requester_display_name: str | None = None
    created_at: datetime
    responded_at: datetime | None


class MediaInput(BaseModel):
    media_type: Literal['image', 'video', 'audio', 'document']
    url: str = Field(min_length=1, max_length=1200)
    thumbnail_url: str | None = Field(default=None, max_length=1200)
    alt_text: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] | None = None
    sort_order: int = Field(default=0, ge=0, le=50)


class MediaRead(BaseModel):
    id: str
    media_type: str
    url: str
    thumbnail_url: str | None
    alt_text: str | None
    metadata: dict[str, Any] | None
    sort_order: int


class PostCreate(BaseModel):
    text: str | None = Field(default=None, max_length=20000)
    type: Literal['text', 'media', 'link', 'poll', 'share'] = 'text'
    visibility: Visibility = 'public'
    page_id: str | None = None
    group_id: str | None = None
    shared_post_id: str | None = None
    location: str | None = Field(default=None, max_length=240)
    feeling: str | None = Field(default=None, max_length=100)
    media: list[MediaInput] = Field(default_factory=list, max_length=10)
    hashtags: list[str] = Field(default_factory=list, max_length=20)
    scheduled_at: datetime | None = None

    @model_validator(mode='after')
    def validate_payload(self):
        if not self.text and not self.media and not self.shared_post_id:
            raise ValueError('A post requires text, media, or a shared post')
        if self.page_id and self.group_id:
            raise ValueError('A post cannot target both a page and a group')
        return self


class PostUpdate(BaseModel):
    text: str | None = Field(default=None, max_length=20000)
    visibility: Visibility | None = None
    location: str | None = Field(default=None, max_length=240)
    feeling: str | None = Field(default=None, max_length=100)
    status: Literal['published', 'draft', 'archived'] | None = None


class ReactionSummary(BaseModel):
    reaction: str
    count: int
    reacted_by_me: bool = False


class AuthorSummary(BaseModel):
    user_id: str
    username: str
    display_name: str
    avatar_url: str | None
    is_verified: bool = False


class PostRead(BaseModel):
    id: str
    author: AuthorSummary
    page_id: str | None
    page_name: str | None = None
    group_id: str | None
    group_name: str | None = None
    shared_post_id: str | None
    type: str
    text: str | None
    visibility: str
    status: str
    location: str | None
    feeling: str | None
    reaction_count: int
    comment_count: int
    share_count: int
    view_count: int
    reactions: list[ReactionSummary] = []
    media: list[MediaRead] = []
    hashtags: list[str] = []
    saved_by_me: bool = False
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class PaginatedPosts(BaseModel):
    items: list[PostRead]
    page: int
    page_size: int
    total: int
    has_more: bool


class ReactionRequest(BaseModel):
    reaction: ReactionType = 'like'


class CommentCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    parent_id: str | None = None


class CommentUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


class CommentRead(BaseModel):
    id: str
    post_id: str
    author: AuthorSummary
    parent_id: str | None
    text: str
    status: str
    reaction_count: int
    reply_count: int
    reactions: list[ReactionSummary] = []
    created_at: datetime
    updated_at: datetime


class PaginatedComments(BaseModel):
    items: list[CommentRead]
    page: int
    page_size: int
    total: int
    has_more: bool


class SavePostRequest(BaseModel):
    collection: str = Field(default='default', min_length=1, max_length=100)


class PageCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    slug: str = Field(min_length=2, max_length=120, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    category: str = Field(min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    avatar_url: str | None = Field(default=None, max_length=1000)
    cover_url: str | None = Field(default=None, max_length=1000)
    website: str | None = Field(default=None, max_length=500)


class PageUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    category: str | None = Field(default=None, min_length=2, max_length=100)
    description: str | None = Field(default=None, max_length=5000)
    avatar_url: str | None = Field(default=None, max_length=1000)
    cover_url: str | None = Field(default=None, max_length=1000)
    website: str | None = Field(default=None, max_length=500)
    status: Literal['active', 'hidden', 'suspended'] | None = None


class PageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    owner_id: str
    name: str
    slug: str
    category: str
    description: str | None
    avatar_url: str | None
    cover_url: str | None
    website: str | None
    status: str
    is_verified: bool
    follower_count: int
    post_count: int
    followed_by_me: bool = False
    my_role: str | None = None
    created_at: datetime
    updated_at: datetime


class PageAdminCreate(BaseModel):
    user_id: str
    role: Literal['admin', 'editor', 'analyst'] = 'editor'


class PageAdminRead(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    role: str
    created_at: datetime


class GroupCreate(BaseModel):
    name: str = Field(min_length=2, max_length=180)
    slug: str = Field(min_length=2, max_length=120, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')
    description: str | None = Field(default=None, max_length=5000)
    privacy: Literal['public', 'private', 'hidden'] = 'public'
    approval_required: bool = False
    avatar_url: str | None = Field(default=None, max_length=1000)
    cover_url: str | None = Field(default=None, max_length=1000)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=180)
    description: str | None = Field(default=None, max_length=5000)
    privacy: Literal['public', 'private', 'hidden'] | None = None
    approval_required: bool | None = None
    avatar_url: str | None = Field(default=None, max_length=1000)
    cover_url: str | None = Field(default=None, max_length=1000)
    status: Literal['active', 'archived', 'suspended'] | None = None


class GroupRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    owner_id: str
    name: str
    slug: str
    description: str | None
    privacy: str
    approval_required: bool
    avatar_url: str | None
    cover_url: str | None
    status: str
    member_count: int
    post_count: int
    my_role: str | None = None
    membership_status: str | None = None
    created_at: datetime
    updated_at: datetime


class GroupMemberRead(BaseModel):
    id: str
    user_id: str
    username: str
    display_name: str
    role: str
    status: str
    joined_at: datetime


class GroupMemberUpdate(BaseModel):
    role: Literal['admin', 'moderator', 'member'] | None = None
    status: Literal['active', 'pending', 'muted', 'banned'] | None = None


class StoryCreate(BaseModel):
    media_type: Literal['image', 'video']
    media_url: str = Field(min_length=1, max_length=1200)
    thumbnail_url: str | None = Field(default=None, max_length=1200)
    caption: str | None = Field(default=None, max_length=1000)
    visibility: Visibility = 'friends'
    duration_hours: int = Field(default=24, ge=1, le=72)


class StoryRead(BaseModel):
    id: str
    author: AuthorSummary
    media_type: str
    media_url: str
    thumbnail_url: str | None
    caption: str | None
    visibility: str
    view_count: int
    viewed_by_me: bool = False
    created_at: datetime
    expires_at: datetime


class StoryViewerRead(BaseModel):
    user_id: str
    username: str
    display_name: str
    avatar_url: str | None
    viewed_at: datetime


class ReportCreate(BaseModel):
    resource_type: Literal['post', 'comment', 'story', 'page', 'group', 'user']
    resource_id: str
    reason: Literal['spam', 'harassment', 'hate', 'violence', 'sexual_content', 'misinformation', 'impersonation', 'privacy', 'illegal_content', 'other']
    details: str | None = Field(default=None, max_length=3000)


class ReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    reporter_id: str
    resource_type: str
    resource_id: str
    reason: str
    details: str | None
    status: str
    resolved_by: str | None
    resolution: str | None
    created_at: datetime
    resolved_at: datetime | None


class ReportUpdate(BaseModel):
    status: Literal['reviewing', 'resolved', 'dismissed']
    resolution: str | None = Field(default=None, max_length=3000)
    remove_content: bool = False


class SocialDashboard(BaseModel):
    users_total: int
    active_users: int
    posts_total: int
    posts_last_24h: int
    comments_total: int
    reactions_total: int
    friendships_total: int
    groups_total: int
    pages_total: int
    active_stories: int
    open_reports: int


class CreatorAnalytics(BaseModel):
    posts_total: int
    reactions_total: int
    comments_total: int
    shares_total: int
    views_total: int
    followers_total: int
    top_posts: list[dict[str, Any]]


class TrendingHashtag(BaseModel):
    name: str
    post_count: int
