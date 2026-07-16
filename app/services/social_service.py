from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    CommentReaction,
    Follow,
    Friendship,
    FriendRequest,
    GroupMember,
    Hashtag,
    Notification,
    PageAdmin,
    PageFollow,
    PostComment,
    PostHashtag,
    PostMedia,
    PostReaction,
    SavedPost,
    SocialGroup,
    SocialPage,
    SocialPost,
    SocialProfile,
    StoryView,
    User,
    UserBlock,
    utcnow,
)

HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]{1,100})")


def display_name(user: User, profile: SocialProfile | None = None) -> str:
    return (profile.display_name if profile and profile.display_name else f'{user.first_name} {user.last_name}').strip()


def ensure_profile(db: Session, user: User) -> SocialProfile:
    profile = db.get(SocialProfile, user.id)
    if not profile:
        profile = SocialProfile(user_id=user.id, display_name=f'{user.first_name} {user.last_name}'.strip())
        db.add(profile)
        db.flush()
    return profile


def pair_ids(first: str, second: str) -> tuple[str, str]:
    return tuple(sorted((first, second)))  # type: ignore[return-value]


def friendship_for(db: Session, first: str, second: str) -> Friendship | None:
    low, high = pair_ids(first, second)
    return db.scalar(select(Friendship).where(Friendship.user_low_id == low, Friendship.user_high_id == high))


def is_friend(db: Session, first: str, second: str) -> bool:
    return friendship_for(db, first, second) is not None


def is_following(db: Session, follower_id: str, following_id: str) -> bool:
    return db.scalar(select(Follow.id).where(Follow.follower_id == follower_id, Follow.following_id == following_id)) is not None


def is_blocked(db: Session, first: str, second: str) -> bool:
    return db.scalar(
        select(UserBlock.id).where(
            or_(
                (UserBlock.blocker_id == first) & (UserBlock.blocked_id == second),
                (UserBlock.blocker_id == second) & (UserBlock.blocked_id == first),
            )
        )
    ) is not None


def notify(db: Session, user_id: str, kind: str, title: str, body: str, data: dict | None = None) -> None:
    db.add(Notification(user_id=user_id, type=kind, title=title, body=body, data_json=data))


def author_summary(db: Session, user_id: str) -> dict:
    user = db.get(User, user_id)
    if not user:
        return {'user_id': user_id, 'username': 'deleted-user', 'display_name': 'Deleted User', 'avatar_url': None, 'is_verified': False}
    profile = ensure_profile(db, user)
    return {
        'user_id': user.id,
        'username': user.username,
        'display_name': display_name(user, profile),
        'avatar_url': profile.avatar_url,
        'is_verified': profile.is_verified,
    }


def profile_to_read(db: Session, profile: SocialProfile, viewer_id: str | None = None) -> dict:
    user = db.get(User, profile.user_id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    friend_status = None
    friend = False
    following = False
    if viewer_id and viewer_id != user.id:
        friend = is_friend(db, viewer_id, user.id)
        following = is_following(db, viewer_id, user.id)
        request = db.scalar(
            select(FriendRequest).where(
                or_(
                    (FriendRequest.requester_id == viewer_id) & (FriendRequest.addressee_id == user.id),
                    (FriendRequest.requester_id == user.id) & (FriendRequest.addressee_id == viewer_id),
                ),
                FriendRequest.status == 'pending',
            )
        )
        if request:
            friend_status = 'outgoing' if request.requester_id == viewer_id else 'incoming'
        elif friend:
            friend_status = 'friends'
    return {
        'user_id': user.id,
        'username': user.username,
        'display_name': display_name(user, profile),
        'bio': profile.bio,
        'avatar_url': profile.avatar_url,
        'cover_url': profile.cover_url,
        'city': profile.city,
        'country': profile.country,
        'website': profile.website,
        'profile_visibility': profile.profile_visibility,
        'message_permission': profile.message_permission,
        'friend_list_visibility': profile.friend_list_visibility,
        'is_verified': profile.is_verified,
        'follower_count': profile.follower_count,
        'following_count': profile.following_count,
        'friend_count': profile.friend_count,
        'post_count': profile.post_count,
        'is_following': following,
        'is_friend': friend,
        'friendship_status': friend_status,
        'created_at': profile.created_at,
        'updated_at': profile.updated_at,
    }


def can_view_profile(db: Session, profile: SocialProfile, viewer_id: str | None) -> bool:
    if profile.profile_visibility == 'public' or viewer_id == profile.user_id:
        return True
    if not viewer_id:
        return False
    if profile.profile_visibility == 'friends':
        return is_friend(db, viewer_id, profile.user_id)
    if profile.profile_visibility == 'followers':
        return is_following(db, viewer_id, profile.user_id)
    return False


def can_view_post(db: Session, post: SocialPost, viewer_id: str | None) -> bool:
    if post.status != 'published' or post.deleted_at is not None:
        return viewer_id == post.author_id
    if post.group_id:
        group = db.get(SocialGroup, post.group_id)
        if not group or group.status != 'active':
            return False
        if group.privacy == 'public':
            return True
        if not viewer_id:
            return False
        member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == viewer_id, GroupMember.status == 'active'))
        return member is not None
    if post.page_id:
        page = db.get(SocialPage, post.page_id)
        return bool(page and page.status == 'active')
    if post.visibility == 'public' or viewer_id == post.author_id:
        return True
    if not viewer_id:
        return False
    if post.visibility == 'friends':
        return is_friend(db, viewer_id, post.author_id)
    if post.visibility == 'followers':
        return is_following(db, viewer_id, post.author_id)
    return False


def ensure_page_access(db: Session, page_id: str, user_id: str, roles: set[str] | None = None) -> tuple[SocialPage, str]:
    page = db.get(SocialPage, page_id)
    if not page or page.status == 'suspended':
        raise HTTPException(status_code=404, detail='Page not found')
    if page.owner_id == user_id:
        role = 'owner'
    else:
        admin = db.scalar(select(PageAdmin).where(PageAdmin.page_id == page_id, PageAdmin.user_id == user_id))
        role = admin.role if admin else ''
    if not role or (roles and role not in roles):
        raise HTTPException(status_code=403, detail='Page management permission required')
    return page, role


def ensure_group_member(db: Session, group_id: str, user_id: str, roles: set[str] | None = None) -> tuple[SocialGroup, GroupMember]:
    group = db.get(SocialGroup, group_id)
    if not group or group.status == 'suspended':
        raise HTTPException(status_code=404, detail='Group not found')
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))
    if not member or member.status != 'active':
        raise HTTPException(status_code=403, detail='Active group membership required')
    if roles and member.role not in roles:
        raise HTTPException(status_code=403, detail='Group management permission required')
    return group, member


def sync_hashtags(db: Session, post: SocialPost, requested: list[str], text: str | None) -> None:
    names = {item.strip().lstrip('#').lower() for item in requested if item.strip()}
    if text:
        names.update(match.group(1).lower() for match in HASHTAG_RE.finditer(text))
    names = {name for name in names if name}
    for name in names:
        hashtag = db.scalar(select(Hashtag).where(Hashtag.name == name))
        if not hashtag:
            hashtag = Hashtag(name=name)
            db.add(hashtag)
            db.flush()
        if not db.scalar(select(PostHashtag.id).where(PostHashtag.post_id == post.id, PostHashtag.hashtag_id == hashtag.id)):
            db.add(PostHashtag(post_id=post.id, hashtag_id=hashtag.id))
            hashtag.post_count += 1


def post_to_read(db: Session, post: SocialPost, viewer_id: str | None = None) -> dict:
    media = list(db.scalars(select(PostMedia).where(PostMedia.post_id == post.id).order_by(PostMedia.sort_order, PostMedia.created_at)).all())
    reactions = list(db.execute(select(PostReaction.reaction, func.count(PostReaction.id)).where(PostReaction.post_id == post.id).group_by(PostReaction.reaction)).all())
    mine = None
    if viewer_id:
        mine = db.scalar(select(PostReaction.reaction).where(PostReaction.post_id == post.id, PostReaction.user_id == viewer_id))
    tags = list(db.scalars(select(Hashtag.name).join(PostHashtag, PostHashtag.hashtag_id == Hashtag.id).where(PostHashtag.post_id == post.id).order_by(Hashtag.name)).all())
    page = db.get(SocialPage, post.page_id) if post.page_id else None
    group = db.get(SocialGroup, post.group_id) if post.group_id else None
    saved = bool(viewer_id and db.scalar(select(SavedPost.id).where(SavedPost.post_id == post.id, SavedPost.user_id == viewer_id)))
    return {
        'id': post.id,
        'author': author_summary(db, post.author_id),
        'page_id': post.page_id,
        'page_name': page.name if page else None,
        'group_id': post.group_id,
        'group_name': group.name if group else None,
        'shared_post_id': post.shared_post_id,
        'type': post.type,
        'text': post.text,
        'visibility': post.visibility,
        'status': post.status,
        'location': post.location,
        'feeling': post.feeling,
        'reaction_count': post.reaction_count,
        'comment_count': post.comment_count,
        'share_count': post.share_count,
        'view_count': post.view_count,
        'reactions': [{'reaction': kind, 'count': count, 'reacted_by_me': mine == kind} for kind, count in reactions],
        'media': [
            {
                'id': item.id,
                'media_type': item.media_type,
                'url': item.url,
                'thumbnail_url': item.thumbnail_url,
                'alt_text': item.alt_text,
                'metadata': item.metadata_json,
                'sort_order': item.sort_order,
            }
            for item in media
        ],
        'hashtags': tags,
        'saved_by_me': saved,
        'created_at': post.created_at,
        'updated_at': post.updated_at,
        'published_at': post.published_at,
    }


def comment_to_read(db: Session, comment: PostComment, viewer_id: str | None = None) -> dict:
    reactions = list(db.execute(select(CommentReaction.reaction, func.count(CommentReaction.id)).where(CommentReaction.comment_id == comment.id).group_by(CommentReaction.reaction)).all())
    mine = None
    if viewer_id:
        mine = db.scalar(select(CommentReaction.reaction).where(CommentReaction.comment_id == comment.id, CommentReaction.user_id == viewer_id))
    return {
        'id': comment.id,
        'post_id': comment.post_id,
        'author': author_summary(db, comment.author_id),
        'parent_id': comment.parent_id,
        'text': comment.text if comment.deleted_at is None else '[deleted]',
        'status': comment.status,
        'reaction_count': comment.reaction_count,
        'reply_count': comment.reply_count,
        'reactions': [{'reaction': kind, 'count': count, 'reacted_by_me': mine == kind} for kind, count in reactions],
        'created_at': comment.created_at,
        'updated_at': comment.updated_at,
    }
