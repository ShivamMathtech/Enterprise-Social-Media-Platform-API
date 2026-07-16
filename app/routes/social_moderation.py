from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_permissions
from app.models import (
    CommentReaction,
    ContentReport,
    Follow,
    Friendship,
    GroupMember,
    Hashtag,
    PostComment,
    PostReaction,
    SocialGroup,
    SocialPage,
    SocialPost,
    SocialProfile,
    Story,
    User,
    utcnow,
)
from app.schemas import APIMessage
from app.social_schemas import CreatorAnalytics, ReportCreate, ReportRead, ReportUpdate, SocialDashboard, TrendingHashtag
from app.services.audit_service import write_audit
from app.services.social_service import ensure_profile, post_to_read

router = APIRouter(prefix='/social', tags=['Social Moderation & Analytics'])


def resource_exists(db: Session, kind: str, resource_id: str) -> bool:
    mapping = {
        'post': SocialPost,
        'comment': PostComment,
        'story': Story,
        'page': SocialPage,
        'group': SocialGroup,
        'user': User,
    }
    model = mapping[kind]
    return db.get(model, resource_id) is not None


@router.post('/reports', response_model=ReportRead, status_code=201)
def create_report(payload: ReportCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not resource_exists(db, payload.resource_type, payload.resource_id):
        raise HTTPException(status_code=404, detail='Reported resource not found')
    duplicate = db.scalar(select(ContentReport).where(ContentReport.reporter_id == user.id, ContentReport.resource_type == payload.resource_type, ContentReport.resource_id == payload.resource_id, ContentReport.status.in_(['open', 'reviewing'])))
    if duplicate:
        raise HTTPException(status_code=409, detail='You already have an active report for this resource')
    report = ContentReport(reporter_id=user.id, **payload.model_dump())
    db.add(report)
    write_audit(db, 'social.report.created', request, actor_user_id=user.id, resource_type=payload.resource_type, resource_id=payload.resource_id, metadata={'reason': payload.reason})
    db.commit()
    db.refresh(report)
    return report


@router.get('/moderation/reports', response_model=list[ReportRead])
def list_reports(
    status_filter: str | None = Query(default=None, alias='status'),
    resource_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(require_permissions('social.moderate')),
    db: Session = Depends(get_db),
):
    stmt = select(ContentReport)
    if status_filter:
        stmt = stmt.where(ContentReport.status == status_filter)
    if resource_type:
        stmt = stmt.where(ContentReport.resource_type == resource_type)
    return list(db.scalars(stmt.order_by(ContentReport.created_at.desc()).limit(limit)).all())


@router.patch('/moderation/reports/{report_id}', response_model=ReportRead)
def resolve_report(report_id: str, payload: ReportUpdate, request: Request, user: User = Depends(require_permissions('social.moderate')), db: Session = Depends(get_db)):
    report = db.get(ContentReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail='Report not found')
    report.status = payload.status
    report.resolution = payload.resolution
    if payload.status in {'resolved', 'dismissed'}:
        report.resolved_by = user.id
        report.resolved_at = utcnow()
    if payload.remove_content:
        if report.resource_type == 'post':
            post = db.get(SocialPost, report.resource_id)
            if post:
                post.status = 'removed'
                post.deleted_at = utcnow()
        elif report.resource_type == 'comment':
            comment = db.get(PostComment, report.resource_id)
            if comment:
                comment.status = 'removed'
                comment.deleted_at = utcnow()
        elif report.resource_type == 'story':
            story = db.get(Story, report.resource_id)
            if story:
                story.deleted_at = utcnow()
        elif report.resource_type == 'page':
            page = db.get(SocialPage, report.resource_id)
            if page:
                page.status = 'suspended'
        elif report.resource_type == 'group':
            group = db.get(SocialGroup, report.resource_id)
            if group:
                group.status = 'suspended'
        elif report.resource_type == 'user':
            target = db.get(User, report.resource_id)
            if target:
                target.status = 'suspended'
    write_audit(db, 'social.report.resolved', request, actor_user_id=user.id, resource_type=report.resource_type, resource_id=report.resource_id, metadata={'report_id': report.id, 'status': payload.status, 'remove_content': payload.remove_content})
    db.commit()
    db.refresh(report)
    return report


@router.post('/moderation/profiles/{user_id}/verify', response_model=APIMessage)
def verify_profile(user_id: str, request: Request, user: User = Depends(require_permissions('social.moderate')), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail='User not found')
    profile = ensure_profile(db, target)
    profile.is_verified = True
    write_audit(db, 'social.profile.verified', request, actor_user_id=user.id, resource_type='social_profile', resource_id=user_id)
    db.commit()
    return APIMessage(message='Profile verified')


@router.delete('/moderation/profiles/{user_id}/verify', response_model=APIMessage)
def unverify_profile(user_id: str, user: User = Depends(require_permissions('social.moderate')), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail='User not found')
    ensure_profile(db, target).is_verified = False
    db.commit()
    return APIMessage(message='Profile verification removed')


@router.get('/analytics/dashboard', response_model=SocialDashboard)
def platform_dashboard(user: User = Depends(require_permissions('social.admin')), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(hours=24)
    return {
        'users_total': db.scalar(select(func.count(User.id)).where(User.deleted_at.is_(None))) or 0,
        'active_users': db.scalar(select(func.count(User.id)).where(User.status == 'active', User.deleted_at.is_(None))) or 0,
        'posts_total': db.scalar(select(func.count(SocialPost.id)).where(SocialPost.deleted_at.is_(None))) or 0,
        'posts_last_24h': db.scalar(select(func.count(SocialPost.id)).where(SocialPost.created_at >= since, SocialPost.deleted_at.is_(None))) or 0,
        'comments_total': db.scalar(select(func.count(PostComment.id)).where(PostComment.deleted_at.is_(None))) or 0,
        'reactions_total': (db.scalar(select(func.count(PostReaction.id))) or 0) + (db.scalar(select(func.count(CommentReaction.id))) or 0),
        'friendships_total': db.scalar(select(func.count(Friendship.id))) or 0,
        'groups_total': db.scalar(select(func.count(SocialGroup.id)).where(SocialGroup.status == 'active')) or 0,
        'pages_total': db.scalar(select(func.count(SocialPage.id)).where(SocialPage.status == 'active')) or 0,
        'active_stories': db.scalar(select(func.count(Story.id)).where(Story.expires_at > utcnow(), Story.deleted_at.is_(None))) or 0,
        'open_reports': db.scalar(select(func.count(ContentReport.id)).where(ContentReport.status.in_(['open', 'reviewing']))) or 0,
    }


@router.get('/analytics/me', response_model=CreatorAnalytics)
def creator_analytics(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    posts = list(db.scalars(select(SocialPost).where(SocialPost.author_id == user.id, SocialPost.deleted_at.is_(None)).order_by(SocialPost.view_count.desc()).limit(1000)).all())
    profile = ensure_profile(db, user)
    top = sorted(posts, key=lambda p: p.reaction_count + p.comment_count * 2 + p.share_count * 3 + p.view_count // 10, reverse=True)[:10]
    return {
        'posts_total': len(posts),
        'reactions_total': sum(p.reaction_count for p in posts),
        'comments_total': sum(p.comment_count for p in posts),
        'shares_total': sum(p.share_count for p in posts),
        'views_total': sum(p.view_count for p in posts),
        'followers_total': profile.follower_count,
        'top_posts': [{'id': p.id, 'text': (p.text or '')[:160], 'reactions': p.reaction_count, 'comments': p.comment_count, 'shares': p.share_count, 'views': p.view_count, 'published_at': p.published_at} for p in top],
    }


@router.get('/analytics/trending-hashtags', response_model=list[TrendingHashtag])
def trending_hashtags(limit: int = Query(default=20, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tags = list(db.scalars(select(Hashtag).order_by(Hashtag.post_count.desc(), Hashtag.name).limit(limit)).all())
    return [{'name': item.name, 'post_count': item.post_count} for item in tags]
