from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import GroupMember, PageAdmin, PageFollow, SocialGroup, SocialPage, SocialPost, User, utcnow
from app.schemas import APIMessage
from app.social_schemas import (
    GroupCreate,
    GroupMemberRead,
    GroupMemberUpdate,
    GroupRead,
    GroupUpdate,
    PageAdminCreate,
    PageAdminRead,
    PageCreate,
    PageRead,
    PageUpdate,
    PaginatedPosts,
)
from app.services.audit_service import write_audit
from app.services.social_service import (
    display_name,
    ensure_group_member,
    ensure_page_access,
    ensure_profile,
    post_to_read,
)

router = APIRouter(prefix='/social', tags=['Pages & Groups'])


def page_to_read(db: Session, page: SocialPage, viewer_id: str | None = None) -> dict:
    followed = bool(viewer_id and db.scalar(select(PageFollow.id).where(PageFollow.page_id == page.id, PageFollow.user_id == viewer_id)))
    role = None
    if viewer_id == page.owner_id:
        role = 'owner'
    elif viewer_id:
        admin = db.scalar(select(PageAdmin).where(PageAdmin.page_id == page.id, PageAdmin.user_id == viewer_id))
        role = admin.role if admin else None
    return {**PageRead.model_validate(page).model_dump(), 'followed_by_me': followed, 'my_role': role}


def group_to_read(db: Session, group: SocialGroup, viewer_id: str | None = None) -> dict:
    member = None
    if viewer_id:
        member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == viewer_id))
    return {**GroupRead.model_validate(group).model_dump(), 'my_role': member.role if member else None, 'membership_status': member.status if member else None}


def member_to_read(db: Session, member: GroupMember) -> dict:
    user = db.get(User, member.user_id)
    profile = ensure_profile(db, user) if user else None
    return {
        'id': member.id,
        'user_id': member.user_id,
        'username': user.username if user else 'deleted-user',
        'display_name': display_name(user, profile) if user and profile else 'Deleted User',
        'role': member.role,
        'status': member.status,
        'joined_at': member.joined_at,
    }


@router.post('/pages', response_model=PageRead, status_code=201)
def create_page(payload: PageCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.scalar(select(SocialPage.id).where(SocialPage.slug == payload.slug)):
        raise HTTPException(status_code=409, detail='Page slug is already in use')
    page = SocialPage(owner_id=user.id, **payload.model_dump())
    db.add(page)
    db.flush()
    db.add(PageAdmin(page_id=page.id, user_id=user.id, role='admin'))
    write_audit(db, 'social.page.created', request, actor_user_id=user.id, resource_type='social_page', resource_id=page.id)
    db.commit()
    db.refresh(page)
    return page_to_read(db, page, user.id)


@router.get('/pages', response_model=list[PageRead])
def list_pages(q: str | None = Query(default=None, max_length=100), category: str | None = Query(default=None, max_length=100), limit: int = Query(default=30, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(SocialPage).where(SocialPage.status == 'active')
    if q:
        stmt = stmt.where(SocialPage.name.ilike(f'%{q}%'))
    if category:
        stmt = stmt.where(SocialPage.category == category)
    pages = list(db.scalars(stmt.order_by(SocialPage.follower_count.desc(), SocialPage.created_at.desc()).limit(limit)).all())
    return [page_to_read(db, page, user.id) for page in pages]


@router.get('/pages/{page_id}', response_model=PageRead)
def get_page(page_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = db.get(SocialPage, page_id)
    if not page or page.status == 'suspended':
        raise HTTPException(status_code=404, detail='Page not found')
    return page_to_read(db, page, user.id)


@router.patch('/pages/{page_id}', response_model=PageRead)
def update_page(page_id: str, payload: PageUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page, _ = ensure_page_access(db, page_id, user.id, {'owner', 'admin', 'editor'})
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(page, field, value)
    page.updated_at = utcnow()
    write_audit(db, 'social.page.updated', request, actor_user_id=user.id, resource_type='social_page', resource_id=page.id)
    db.commit()
    return page_to_read(db, page, user.id)


@router.delete('/pages/{page_id}', response_model=APIMessage)
def delete_page(page_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page, role = ensure_page_access(db, page_id, user.id, {'owner'})
    page.status = 'hidden'
    write_audit(db, 'social.page.deleted', request, actor_user_id=user.id, resource_type='social_page', resource_id=page.id)
    db.commit()
    return APIMessage(message='Page hidden')


@router.post('/pages/{page_id}/follow', response_model=APIMessage, status_code=201)
def follow_page(page_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page = db.get(SocialPage, page_id)
    if not page or page.status != 'active':
        raise HTTPException(status_code=404, detail='Page not found')
    if not db.scalar(select(PageFollow.id).where(PageFollow.page_id == page.id, PageFollow.user_id == user.id)):
        db.add(PageFollow(page_id=page.id, user_id=user.id))
        page.follower_count += 1
        db.commit()
    return APIMessage(message='Page followed')


@router.delete('/pages/{page_id}/follow', response_model=APIMessage)
def unfollow_page(page_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    edge = db.scalar(select(PageFollow).where(PageFollow.page_id == page_id, PageFollow.user_id == user.id))
    if edge:
        page = db.get(SocialPage, page_id)
        db.delete(edge)
        if page:
            page.follower_count = max(0, page.follower_count - 1)
        db.commit()
    return APIMessage(message='Page unfollowed')


@router.get('/pages/{page_id}/posts', response_model=PaginatedPosts)
def page_posts(page_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.get(SocialPage, page_id)
    if not target or target.status != 'active':
        raise HTTPException(status_code=404, detail='Page not found')
    stmt = select(SocialPost).where(SocialPost.page_id == page_id, SocialPost.status == 'published', SocialPost.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    posts = list(db.scalars(stmt.order_by(SocialPost.published_at.desc()).offset((page - 1) * page_size).limit(page_size)).all())
    return {'items': [post_to_read(db, post, user.id) for post in posts], 'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}


@router.get('/pages/{page_id}/admins', response_model=list[PageAdminRead])
def list_page_admins(page_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_page_access(db, page_id, user.id, {'owner', 'admin'})
    admins = list(db.scalars(select(PageAdmin).where(PageAdmin.page_id == page_id).order_by(PageAdmin.created_at)).all())
    result = []
    for admin in admins:
        target = db.get(User, admin.user_id)
        profile = ensure_profile(db, target) if target else None
        result.append({'id': admin.id, 'user_id': admin.user_id, 'username': target.username if target else 'deleted-user', 'display_name': display_name(target, profile) if target and profile else 'Deleted User', 'role': admin.role, 'created_at': admin.created_at})
    return result


@router.post('/pages/{page_id}/admins', response_model=PageAdminRead, status_code=201)
def add_page_admin(page_id: str, payload: PageAdminCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page, _ = ensure_page_access(db, page_id, user.id, {'owner', 'admin'})
    target = db.get(User, payload.user_id)
    if not target or target.status != 'active':
        raise HTTPException(status_code=404, detail='User not found')
    admin = db.scalar(select(PageAdmin).where(PageAdmin.page_id == page.id, PageAdmin.user_id == target.id))
    if admin:
        admin.role = payload.role
    else:
        admin = PageAdmin(page_id=page.id, user_id=target.id, role=payload.role)
        db.add(admin)
    write_audit(db, 'social.page_admin.assigned', request, actor_user_id=user.id, resource_type='social_page', resource_id=page.id, metadata={'target_user_id': target.id, 'role': payload.role})
    db.commit()
    db.refresh(admin)
    profile = ensure_profile(db, target)
    return {'id': admin.id, 'user_id': target.id, 'username': target.username, 'display_name': display_name(target, profile), 'role': admin.role, 'created_at': admin.created_at}


@router.delete('/pages/{page_id}/admins/{admin_user_id}', response_model=APIMessage)
def remove_page_admin(page_id: str, admin_user_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    page, _ = ensure_page_access(db, page_id, user.id, {'owner'})
    if admin_user_id == page.owner_id:
        raise HTTPException(status_code=422, detail='Page owner cannot be removed')
    admin = db.scalar(select(PageAdmin).where(PageAdmin.page_id == page.id, PageAdmin.user_id == admin_user_id))
    if admin:
        db.delete(admin)
        db.commit()
    return APIMessage(message='Page administrator removed')


@router.post('/groups', response_model=GroupRead, status_code=201)
def create_group(payload: GroupCreate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if db.scalar(select(SocialGroup.id).where(SocialGroup.slug == payload.slug)):
        raise HTTPException(status_code=409, detail='Group slug is already in use')
    group = SocialGroup(owner_id=user.id, **payload.model_dump())
    db.add(group)
    db.flush()
    db.add(GroupMember(group_id=group.id, user_id=user.id, role='owner', status='active'))
    write_audit(db, 'social.group.created', request, actor_user_id=user.id, resource_type='social_group', resource_id=group.id)
    db.commit()
    db.refresh(group)
    return group_to_read(db, group, user.id)


@router.get('/groups', response_model=list[GroupRead])
def list_groups(q: str | None = Query(default=None, max_length=100), privacy: str | None = Query(default=None), limit: int = Query(default=30, ge=1, le=100), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(SocialGroup).where(SocialGroup.status == 'active', SocialGroup.privacy != 'hidden')
    if q:
        stmt = stmt.where(SocialGroup.name.ilike(f'%{q}%'))
    if privacy:
        stmt = stmt.where(SocialGroup.privacy == privacy)
    groups = list(db.scalars(stmt.order_by(SocialGroup.member_count.desc(), SocialGroup.created_at.desc()).limit(limit)).all())
    return [group_to_read(db, group, user.id) for group in groups]


@router.get('/groups/{group_id}', response_model=GroupRead)
def get_group(group_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = db.get(SocialGroup, group_id)
    if not group or group.status == 'suspended':
        raise HTTPException(status_code=404, detail='Group not found')
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id))
    if group.privacy == 'hidden' and not member:
        raise HTTPException(status_code=404, detail='Group not found')
    return group_to_read(db, group, user.id)


@router.patch('/groups/{group_id}', response_model=GroupRead)
def update_group(group_id: str, payload: GroupUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, _ = ensure_group_member(db, group_id, user.id, {'owner', 'admin'})
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    group.updated_at = utcnow()
    write_audit(db, 'social.group.updated', request, actor_user_id=user.id, resource_type='social_group', resource_id=group.id)
    db.commit()
    return group_to_read(db, group, user.id)


@router.post('/groups/{group_id}/join', response_model=GroupRead)
def join_group(group_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = db.get(SocialGroup, group_id)
    if not group or group.status != 'active' or group.privacy == 'hidden':
        raise HTTPException(status_code=404, detail='Group not found')
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id))
    desired = 'pending' if group.approval_required or group.privacy == 'private' else 'active'
    if member:
        if member.status == 'banned':
            raise HTTPException(status_code=403, detail='You are banned from this group')
        if member.status not in {'active', 'pending'}:
            member.status = desired
    else:
        member = GroupMember(group_id=group.id, user_id=user.id, role='member', status=desired)
        db.add(member)
        if desired == 'active':
            group.member_count += 1
    db.commit()
    return group_to_read(db, group, user.id)


@router.delete('/groups/{group_id}/leave', response_model=APIMessage)
def leave_group(group_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = db.get(SocialGroup, group_id)
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user.id))
    if not group or not member:
        raise HTTPException(status_code=404, detail='Membership not found')
    if member.role == 'owner':
        raise HTTPException(status_code=422, detail='Transfer ownership before leaving the group')
    if member.status == 'active':
        group.member_count = max(1, group.member_count - 1)
    db.delete(member)
    db.commit()
    return APIMessage(message='Left group')


@router.get('/groups/{group_id}/members', response_model=list[GroupMemberRead])
def list_group_members(group_id: str, status_filter: str | None = Query(default=None, alias='status'), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = db.get(SocialGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail='Group not found')
    viewer_member = db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user.id, GroupMember.status == 'active'))
    if group.privacy != 'public' and not viewer_member:
        raise HTTPException(status_code=403, detail='Group membership required')
    stmt = select(GroupMember).where(GroupMember.group_id == group_id)
    if status_filter:
        stmt = stmt.where(GroupMember.status == status_filter)
    else:
        stmt = stmt.where(GroupMember.status == 'active')
    members = list(db.scalars(stmt.order_by(GroupMember.joined_at)).all())
    return [member_to_read(db, item) for item in members]


@router.patch('/groups/{group_id}/members/{member_user_id}', response_model=GroupMemberRead)
def update_group_member(group_id: str, member_user_id: str, payload: GroupMemberUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group, actor = ensure_group_member(db, group_id, user.id, {'owner', 'admin', 'moderator'})
    target = db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == member_user_id))
    if not target:
        raise HTTPException(status_code=404, detail='Group member not found')
    if target.role == 'owner' and actor.role != 'owner':
        raise HTTPException(status_code=403, detail='Only the owner can manage owner membership')
    before_status = target.status
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(target, field, value)
    if before_status != 'active' and target.status == 'active':
        group.member_count += 1
    elif before_status == 'active' and target.status != 'active':
        group.member_count = max(1, group.member_count - 1)
    write_audit(db, 'social.group_member.updated', request, actor_user_id=user.id, resource_type='social_group', resource_id=group.id, metadata={'target_user_id': member_user_id, **payload.model_dump(exclude_unset=True)})
    db.commit()
    return member_to_read(db, target)


@router.get('/groups/{group_id}/posts', response_model=PaginatedPosts)
def group_posts(group_id: str, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=50), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    group = db.get(SocialGroup, group_id)
    if not group or group.status != 'active':
        raise HTTPException(status_code=404, detail='Group not found')
    member = db.scalar(select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.user_id == user.id, GroupMember.status == 'active'))
    if group.privacy != 'public' and not member:
        raise HTTPException(status_code=403, detail='Group membership required')
    stmt = select(SocialPost).where(SocialPost.group_id == group_id, SocialPost.status == 'published', SocialPost.deleted_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    posts = list(db.scalars(stmt.order_by(SocialPost.published_at.desc()).offset((page - 1) * page_size).limit(page_size)).all())
    return {'items': [post_to_read(db, post, user.id) for post in posts], 'page': page, 'page_size': page_size, 'total': total, 'has_more': page * page_size < total}
