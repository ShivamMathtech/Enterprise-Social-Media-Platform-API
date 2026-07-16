from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import (
    CommentReaction,
    Follow,
    Friendship,
    GroupMember,
    PostComment,
    PostMedia,
    PostReaction,
    PageFollow,
    SavedPost,
    SocialGroup,
    SocialPage,
    SocialPost,
    SocialProfile,
    Story,
    StoryView,
    User,
    utcnow,
)
from app.schemas import APIMessage
from app.social_schemas import (
    CommentCreate,
    CommentRead,
    CommentUpdate,
    PaginatedComments,
    PaginatedPosts,
    PostCreate,
    PostRead,
    PostUpdate,
    ReactionRequest,
    SavePostRequest,
    StoryCreate,
    StoryRead,
    StoryViewerRead,
)
from app.services.audit_service import write_audit
from app.services.social_service import (
    author_summary,
    can_view_post,
    comment_to_read,
    ensure_group_member,
    ensure_page_access,
    ensure_profile,
    is_friend,
    is_following,
    notify,
    post_to_read,
    sync_hashtags,
)

router = APIRouter(prefix='/social', tags=['Posts, Feed & Stories'])
MENTION_RE = re.compile(r'@([A-Za-z0-9_.-]{2,64})')


def require_post(db: Session, post_id: str, viewer_id: str | None = None) -> SocialPost:
    post = db.get(SocialPost, post_id)
    if not post or post.deleted_at is not None or not can_view_post(db, post, viewer_id):
        raise HTTPException(status_code=404, detail='Post not found')
    return post


def notify_mentions(db: Session, text: str | None, actor: User, resource_type: str, resource_id: str) -> None:
    if not text:
        return
    usernames = set(MENTION_RE.findall(text))
    for username in usernames:
        mentioned = db.scalar(select(User).where(User.username == username, User.status == 'active'))
        if mentioned and mentioned.id != actor.id:
            notify(db, mentioned.id, 'social.mention', 'You were mentioned', f'{actor.username} mentioned you in a {resource_type}.', {'resource_type': resource_type, 'resource_id': resource_id})


@router.post('/posts', response_model=PostRead, status_code=201)
def create_post(payload: PostCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = None
    group = None
    if payload.page_id:
        page, _ = ensure_page_access(db, payload.page_id, user.id, {'owner', 'admin', 'editor'})
    if payload.group_id:
        group, _ = ensure_group_member(db, payload.group_id, user.id)
    shared = None
    if payload.shared_post_id:
        shared = require_post(db, payload.shared_post_id, user.id)
    status_value = 'scheduled' if payload.scheduled_at and payload.scheduled_at > utcnow() else 'published'
    published_at = None if status_value == 'scheduled' else utcnow()
    post = SocialPost(
        author_id=user.id,
        page_id=payload.page_id,
        group_id=payload.group_id,
        shared_post_id=payload.shared_post_id,
        type='share' if payload.shared_post_id else payload.type,
        text=payload.text,
        visibility=payload.visibility,
        status=status_value,
        location=payload.location,
        feeling=payload.feeling,
        scheduled_at=payload.scheduled_at,
        published_at=published_at,
    )
    db.add(post)
    db.flush()
    for media in payload.media:
        db.add(PostMedia(post_id=post.id, media_type=media.media_type, url=media.url, thumbnail_url=media.thumbnail_url, alt_text=media.alt_text, metadata_json=media.metadata, sort_order=media.sort_order))
    sync_hashtags(db, post, payload.hashtags, payload.text)
    if status_value == 'published':
        ensure_profile(db, user).post_count += 1
        if page:
            page.post_count += 1
        if group:
            group.post_count += 1
        if shared:
            shared.share_count += 1
    notify_mentions(db, payload.text, user, 'post', post.id)
    write_audit(db, 'social.post.created', request, actor_user_id=user.id, resource_type='social_post', resource_id=post.id, metadata={'page_id': post.page_id, 'group_id': post.group_id, 'visibility': post.visibility})
    db.commit()
    db.refresh(post)
    return post_to_read(db, post, user.id)


@router.get('/posts/feed', response_model=PaginatedPosts)
def home_feed(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    followed = set(db.scalars(select(Follow.following_id).where(Follow.follower_id == user.id)).all())
    friend_rows = list(db.scalars(select(Friendship).where(or_(Friendship.user_low_id == user.id, Friendship.user_high_id == user.id))).all())
    network = followed | {r.user_high_id if r.user_low_id == user.id else r.user_low_id for r in friend_rows} | {user.id}
    page_ids = set(db.scalars(select(PageFollow.page_id).where(PageFollow.user_id == user.id)).all())
    group_ids = set(db.scalars(select(GroupMember.group_id).where(GroupMember.user_id == user.id, GroupMember.status == 'active')).all())
    candidates = list(
        db.scalars(
            select(SocialPost)
            .where(
                SocialPost.deleted_at.is_(None),
                SocialPost.status == 'published',
                or_(
                    SocialPost.visibility == 'public',
                    SocialPost.author_id.in_(network),
                    SocialPost.page_id.in_(page_ids) if page_ids else False,
                    SocialPost.group_id.in_(group_ids) if group_ids else False,
                ),
            )
            .order_by(SocialPost.published_at.desc(), SocialPost.created_at.desc())
            .limit(page * page_size * 3)
        ).all()
    )
    visible = [post for post in candidates if can_view_post(db, post, user.id)]
    total = len(visible)
    start = (page - 1) * page_size
    items = visible[start:start + page_size]
    return {'items': [post_to_read(db, post, user.id) for post in items], 'page': page, 'page_size': page_size, 'total': total, 'has_more': start + page_size < total}


@router.get('/posts/explore', response_model=PaginatedPosts)
def explore_posts(
    hashtag: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(SocialPost).where(SocialPost.deleted_at.is_(None), SocialPost.status == 'published', SocialPost.visibility == 'public')
    if hashtag:
        from app.models import Hashtag, PostHashtag
        stmt = stmt.join(PostHashtag, PostHashtag.post_id == SocialPost.id).join(Hashtag, Hashtag.id == PostHashtag.hashtag_id).where(Hashtag.name == hashtag.lower().lstrip('#'))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    items = list(db.scalars(stmt.order_by((SocialPost.reaction_count + SocialPost.comment_count * 2 + SocialPost.share_count * 3).desc(), SocialPost.published_at.desc()).offset((page - 1) * page_size).limit(page_size)).all())
    return {'items': [post_to_read(db, item, user.id) for item in items], 'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}


@router.get('/posts/saved', response_model=PaginatedPosts)
def saved_posts(
    collection: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(SavedPost).where(SavedPost.user_id == user.id)
    if collection:
        stmt = stmt.where(SavedPost.collection == collection)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    saves = list(db.scalars(stmt.order_by(SavedPost.created_at.desc()).offset((page - 1) * page_size).limit(page_size)).all())
    posts = [db.get(SocialPost, save.post_id) for save in saves]
    visible = [post for post in posts if post and can_view_post(db, post, user.id)]
    return {'items': [post_to_read(db, post, user.id) for post in visible], 'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}


@router.get('/profiles/{user_id}/posts', response_model=PaginatedPosts)
def user_posts(user_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(SocialPost).where(SocialPost.author_id == user_id, SocialPost.page_id.is_(None), SocialPost.group_id.is_(None), SocialPost.deleted_at.is_(None), SocialPost.status == 'published')
    candidates = list(db.scalars(stmt.order_by(SocialPost.published_at.desc()).offset((page - 1) * page_size * 2).limit(page_size * 2)).all())
    visible = [post for post in candidates if can_view_post(db, post, user.id)][:page_size]
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    return {'items': [post_to_read(db, post, user.id) for post in visible], 'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}


@router.get('/posts/{post_id}', response_model=PostRead)
def get_post(post_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = require_post(db, post_id, user.id)
    post.view_count += 1
    db.commit()
    return post_to_read(db, post, user.id)


@router.patch('/posts/{post_id}', response_model=PostRead)
def update_post(post_id: str, payload: PostUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = db.get(SocialPost, post_id)
    if not post or post.deleted_at is not None:
        raise HTTPException(status_code=404, detail='Post not found')
    if post.author_id != user.id:
        if post.page_id:
            ensure_page_access(db, post.page_id, user.id, {'owner', 'admin', 'editor'})
        elif post.group_id:
            ensure_group_member(db, post.group_id, user.id, {'owner', 'admin', 'moderator'})
        else:
            raise HTTPException(status_code=403, detail='You cannot edit this post')
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(post, field, value)
    post.updated_at = utcnow()
    notify_mentions(db, payload.text, user, 'post', post.id)
    write_audit(db, 'social.post.updated', request, actor_user_id=user.id, resource_type='social_post', resource_id=post.id)
    db.commit()
    return post_to_read(db, post, user.id)


@router.delete('/posts/{post_id}', response_model=APIMessage)
def delete_post(post_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = db.get(SocialPost, post_id)
    if not post or post.deleted_at is not None:
        raise HTTPException(status_code=404, detail='Post not found')
    allowed = post.author_id == user.id
    if not allowed and post.page_id:
        try:
            ensure_page_access(db, post.page_id, user.id, {'owner', 'admin'})
            allowed = True
        except HTTPException:
            pass
    if not allowed and post.group_id:
        try:
            ensure_group_member(db, post.group_id, user.id, {'owner', 'admin', 'moderator'})
            allowed = True
        except HTTPException:
            pass
    if not allowed:
        raise HTTPException(status_code=403, detail='You cannot delete this post')
    post.deleted_at = utcnow()
    post.status = 'deleted'
    ensure_profile(db, db.get(User, post.author_id)).post_count = max(0, ensure_profile(db, db.get(User, post.author_id)).post_count - 1)
    if post.page_id:
        page = db.get(SocialPage, post.page_id)
        if page:
            page.post_count = max(0, page.post_count - 1)
    if post.group_id:
        group = db.get(SocialGroup, post.group_id)
        if group:
            group.post_count = max(0, group.post_count - 1)
    write_audit(db, 'social.post.deleted', request, actor_user_id=user.id, resource_type='social_post', resource_id=post.id)
    db.commit()
    return APIMessage(message='Post deleted')


@router.put('/posts/{post_id}/reaction', response_model=PostRead)
def react_to_post(post_id: str, payload: ReactionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = require_post(db, post_id, user.id)
    reaction = db.scalar(select(PostReaction).where(PostReaction.post_id == post.id, PostReaction.user_id == user.id))
    if reaction:
        reaction.reaction = payload.reaction
    else:
        reaction = PostReaction(post_id=post.id, user_id=user.id, reaction=payload.reaction)
        db.add(reaction)
        post.reaction_count += 1
        if post.author_id != user.id:
            notify(db, post.author_id, 'social.post_reaction', 'New reaction', f'{user.username} reacted to your post.', {'post_id': post.id, 'reaction': payload.reaction})
    db.commit()
    return post_to_read(db, post, user.id)


@router.delete('/posts/{post_id}/reaction', response_model=PostRead)
def remove_post_reaction(post_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = require_post(db, post_id, user.id)
    reaction = db.scalar(select(PostReaction).where(PostReaction.post_id == post.id, PostReaction.user_id == user.id))
    if reaction:
        db.delete(reaction)
        post.reaction_count = max(0, post.reaction_count - 1)
        db.commit()
    return post_to_read(db, post, user.id)


@router.put('/posts/{post_id}/save', response_model=APIMessage)
def save_post(post_id: str, payload: SavePostRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = require_post(db, post_id, user.id)
    saved = db.scalar(select(SavedPost).where(SavedPost.post_id == post.id, SavedPost.user_id == user.id))
    if saved:
        saved.collection = payload.collection
    else:
        db.add(SavedPost(post_id=post.id, user_id=user.id, collection=payload.collection))
    db.commit()
    return APIMessage(message='Post saved')


@router.delete('/posts/{post_id}/save', response_model=APIMessage)
def unsave_post(post_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    saved = db.scalar(select(SavedPost).where(SavedPost.post_id == post_id, SavedPost.user_id == user.id))
    if saved:
        db.delete(saved)
        db.commit()
    return APIMessage(message='Post removed from saved items')


@router.post('/posts/{post_id}/comments', response_model=CommentRead, status_code=201)
def create_comment(post_id: str, payload: CommentCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    post = require_post(db, post_id, user.id)
    parent = None
    if payload.parent_id:
        parent = db.get(PostComment, payload.parent_id)
        if not parent or parent.post_id != post.id or parent.deleted_at is not None:
            raise HTTPException(status_code=404, detail='Parent comment not found')
    comment = PostComment(post_id=post.id, author_id=user.id, parent_id=payload.parent_id, text=payload.text)
    db.add(comment)
    db.flush()
    post.comment_count += 1
    if parent:
        parent.reply_count += 1
        if parent.author_id != user.id:
            notify(db, parent.author_id, 'social.comment_reply', 'New reply', f'{user.username} replied to your comment.', {'post_id': post.id, 'comment_id': comment.id})
    elif post.author_id != user.id:
        notify(db, post.author_id, 'social.post_comment', 'New comment', f'{user.username} commented on your post.', {'post_id': post.id, 'comment_id': comment.id})
    notify_mentions(db, payload.text, user, 'comment', comment.id)
    db.commit()
    db.refresh(comment)
    return comment_to_read(db, comment, user.id)


@router.get('/posts/{post_id}/comments', response_model=PaginatedComments)
def list_comments(post_id: str, parent_id: str | None = None, page: int = Query(default=1, ge=1), page_size: int = Query(default=30, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    require_post(db, post_id, user.id)
    stmt = select(PostComment).where(PostComment.post_id == post_id, PostComment.parent_id == parent_id, PostComment.status == 'active')
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = list(db.scalars(stmt.order_by(PostComment.created_at.asc()).offset((page - 1) * page_size).limit(page_size)).all())
    return {'items': [comment_to_read(db, item, user.id) for item in items], 'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}


@router.patch('/comments/{comment_id}', response_model=CommentRead)
def update_comment(comment_id: str, payload: CommentUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment = db.get(PostComment, comment_id)
    if not comment or comment.deleted_at is not None:
        raise HTTPException(status_code=404, detail='Comment not found')
    if comment.author_id != user.id:
        raise HTTPException(status_code=403, detail='You cannot edit this comment')
    comment.text = payload.text
    comment.updated_at = utcnow()
    notify_mentions(db, payload.text, user, 'comment', comment.id)
    db.commit()
    return comment_to_read(db, comment, user.id)


@router.delete('/comments/{comment_id}', response_model=APIMessage)
def delete_comment(comment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment = db.get(PostComment, comment_id)
    if not comment or comment.deleted_at is not None:
        raise HTTPException(status_code=404, detail='Comment not found')
    post = db.get(SocialPost, comment.post_id)
    allowed = comment.author_id == user.id or (post and post.author_id == user.id)
    if not allowed:
        raise HTTPException(status_code=403, detail='You cannot delete this comment')
    comment.deleted_at = utcnow()
    comment.status = 'deleted'
    if post:
        post.comment_count = max(0, post.comment_count - 1)
    if comment.parent_id:
        parent = db.get(PostComment, comment.parent_id)
        if parent:
            parent.reply_count = max(0, parent.reply_count - 1)
    db.commit()
    return APIMessage(message='Comment deleted')


@router.put('/comments/{comment_id}/reaction', response_model=CommentRead)
def react_to_comment(comment_id: str, payload: ReactionRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment = db.get(PostComment, comment_id)
    if not comment or comment.deleted_at is not None:
        raise HTTPException(status_code=404, detail='Comment not found')
    require_post(db, comment.post_id, user.id)
    reaction = db.scalar(select(CommentReaction).where(CommentReaction.comment_id == comment.id, CommentReaction.user_id == user.id))
    if reaction:
        reaction.reaction = payload.reaction
    else:
        db.add(CommentReaction(comment_id=comment.id, user_id=user.id, reaction=payload.reaction))
        comment.reaction_count += 1
        if comment.author_id != user.id:
            notify(db, comment.author_id, 'social.comment_reaction', 'New reaction', f'{user.username} reacted to your comment.', {'comment_id': comment.id})
    db.commit()
    return comment_to_read(db, comment, user.id)


@router.delete('/comments/{comment_id}/reaction', response_model=CommentRead)
def remove_comment_reaction(comment_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    comment = db.get(PostComment, comment_id)
    if not comment:
        raise HTTPException(status_code=404, detail='Comment not found')
    reaction = db.scalar(select(CommentReaction).where(CommentReaction.comment_id == comment.id, CommentReaction.user_id == user.id))
    if reaction:
        db.delete(reaction)
        comment.reaction_count = max(0, comment.reaction_count - 1)
        db.commit()
    return comment_to_read(db, comment, user.id)


@router.post('/stories', response_model=StoryRead, status_code=201)
def create_story(payload: StoryCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = Story(author_id=user.id, media_type=payload.media_type, media_url=payload.media_url, thumbnail_url=payload.thumbnail_url, caption=payload.caption, visibility=payload.visibility, expires_at=utcnow() + timedelta(hours=payload.duration_hours))
    db.add(story)
    db.commit()
    db.refresh(story)
    return {**story_to_read(db, story, user.id)}


def story_to_read(db: Session, story: Story, viewer_id: str | None = None) -> dict:
    viewed = bool(viewer_id and db.scalar(select(StoryView.id).where(StoryView.story_id == story.id, StoryView.viewer_id == viewer_id)))
    return {'id': story.id, 'author': author_summary(db, story.author_id), 'media_type': story.media_type, 'media_url': story.media_url, 'thumbnail_url': story.thumbnail_url, 'caption': story.caption, 'visibility': story.visibility, 'view_count': story.view_count, 'viewed_by_me': viewed, 'created_at': story.created_at, 'expires_at': story.expires_at}


@router.get('/stories/feed', response_model=list[StoryRead])
def stories_feed(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    followed = set(db.scalars(select(Follow.following_id).where(Follow.follower_id == user.id)).all())
    friend_rows = list(db.scalars(select(Friendship).where(or_(Friendship.user_low_id == user.id, Friendship.user_high_id == user.id))).all())
    friends = {row.user_high_id if row.user_low_id == user.id else row.user_low_id for row in friend_rows}
    author_ids = followed | friends | {user.id}
    stories = list(db.scalars(select(Story).where(Story.author_id.in_(author_ids), Story.expires_at > utcnow(), Story.deleted_at.is_(None)).order_by(Story.created_at.desc())).all())
    visible = [s for s in stories if s.author_id == user.id or s.visibility == 'public' or (s.visibility == 'friends' and s.author_id in friends) or (s.visibility == 'followers' and s.author_id in followed)]
    return [story_to_read(db, item, user.id) for item in visible]


@router.get('/stories/{story_id}', response_model=StoryRead)
def get_story(story_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = db.get(Story, story_id)
    expires_at = story.expires_at.replace(tzinfo=utcnow().tzinfo) if story and story.expires_at.tzinfo is None else (story.expires_at if story else None)
    if not story or story.deleted_at is not None or expires_at <= utcnow():
        raise HTTPException(status_code=404, detail='Story not found')
    allowed = story.author_id == user.id or story.visibility == 'public' or (story.visibility == 'friends' and is_friend(db, user.id, story.author_id)) or (story.visibility == 'followers' and is_following(db, user.id, story.author_id))
    if not allowed:
        raise HTTPException(status_code=403, detail='Story is not visible to you')
    if story.author_id != user.id:
        existing = db.scalar(select(StoryView).where(StoryView.story_id == story.id, StoryView.viewer_id == user.id))
        if not existing:
            db.add(StoryView(story_id=story.id, viewer_id=user.id))
            story.view_count += 1
            db.commit()
    return story_to_read(db, story, user.id)


@router.get('/stories/{story_id}/viewers', response_model=list[StoryViewerRead])
def story_viewers(story_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = db.get(Story, story_id)
    if not story or story.author_id != user.id:
        raise HTTPException(status_code=404, detail='Story not found')
    rows = list(db.scalars(select(StoryView).where(StoryView.story_id == story.id).order_by(StoryView.viewed_at.desc())).all())
    result = []
    for view in rows:
        author = author_summary(db, view.viewer_id)
        result.append({**author, 'viewed_at': view.viewed_at})
    return result


@router.delete('/stories/{story_id}', response_model=APIMessage)
def delete_story(story_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    story = db.get(Story, story_id)
    if not story or story.author_id != user.id:
        raise HTTPException(status_code=404, detail='Story not found')
    story.deleted_at = utcnow()
    db.commit()
    return APIMessage(message='Story deleted')
