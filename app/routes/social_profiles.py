from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Follow, FriendRequest, Friendship, SocialProfile, User, utcnow
from app.schemas import APIMessage
from app.social_schemas import FriendRequestRead, ProfileRead, ProfileUpdate, RelationshipRead
from app.services.audit_service import write_audit
from app.services.social_service import (
    display_name,
    ensure_profile,
    friendship_for,
    is_blocked,
    notify,
    pair_ids,
    profile_to_read,
    can_view_profile,
)

router = APIRouter(prefix='/social', tags=['Social Profiles & Relationships'])


def relationship_read(db: Session, user_id: str, *, followed_at=None, friended_at=None) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail='User not found')
    profile = ensure_profile(db, target)
    return {
        'user_id': target.id,
        'username': target.username,
        'display_name': display_name(target, profile),
        'avatar_url': profile.avatar_url,
        'is_verified': profile.is_verified,
        'followed_at': followed_at,
        'friended_at': friended_at,
    }


@router.get('/profiles/me', response_model=ProfileRead)
def my_profile(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = ensure_profile(db, user)
    db.commit()
    return profile_to_read(db, profile, user.id)


@router.patch('/profiles/me', response_model=ProfileRead)
def update_profile(payload: ProfileUpdate, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = ensure_profile(db, user)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    profile.updated_at = utcnow()
    write_audit(db, 'social.profile.updated', request, actor_user_id=user.id, resource_type='social_profile', resource_id=user.id)
    db.commit()
    return profile_to_read(db, profile, user.id)


@router.get('/profiles/{username}', response_model=ProfileRead)
def get_profile(username: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.scalar(select(User).where(User.username == username, User.status == 'active', User.deleted_at.is_(None)))
    if not target or is_blocked(db, user.id, target.id):
        raise HTTPException(status_code=404, detail='Profile not found')
    profile = ensure_profile(db, target)
    if not can_view_profile(db, profile, user.id):
        raise HTTPException(status_code=403, detail='This profile is not visible to you')
    return profile_to_read(db, profile, user.id)


@router.get('/people/discover', response_model=list[ProfileRead])
def discover_people(
    q: str | None = Query(default=None, max_length=100),
    city: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    followed_ids = set(db.scalars(select(Follow.following_id).where(Follow.follower_id == user.id)).all())
    stmt = select(User).where(User.id != user.id, User.status == 'active', User.deleted_at.is_(None))
    if q:
        stmt = stmt.where(or_(User.username.ilike(f'%{q}%'), User.first_name.ilike(f'%{q}%'), User.last_name.ilike(f'%{q}%')))
    users = list(db.scalars(stmt.order_by(User.created_at.desc()).limit(limit * 2)).all())
    results = []
    for target in users:
        if target.id in followed_ids or is_blocked(db, user.id, target.id):
            continue
        profile = ensure_profile(db, target)
        if city and (profile.city or '').lower() != city.lower():
            continue
        if profile.profile_visibility == 'private':
            continue
        results.append(profile_to_read(db, profile, user.id))
        if len(results) >= limit:
            break
    return results


@router.post('/profiles/{user_id}/follow', response_model=APIMessage, status_code=201)
def follow_user(user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(status_code=422, detail='You cannot follow yourself')
    target = db.get(User, user_id)
    if not target or target.status != 'active' or is_blocked(db, user.id, user_id):
        raise HTTPException(status_code=404, detail='User not found')
    existing = db.scalar(select(Follow).where(Follow.follower_id == user.id, Follow.following_id == user_id))
    if not existing:
        db.add(Follow(follower_id=user.id, following_id=user_id))
        mine = ensure_profile(db, user)
        theirs = ensure_profile(db, target)
        mine.following_count += 1
        theirs.follower_count += 1
        notify(db, user_id, 'social.follow', 'New follower', f'{user.username} started following you.', {'user_id': user.id})
        write_audit(db, 'social.follow.created', request, actor_user_id=user.id, resource_type='user', resource_id=user_id)
        db.commit()
    return APIMessage(message='Following user')


@router.delete('/profiles/{user_id}/follow', response_model=APIMessage)
def unfollow_user(user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    edge = db.scalar(select(Follow).where(Follow.follower_id == user.id, Follow.following_id == user_id))
    if edge:
        db.delete(edge)
        target = db.get(User, user_id)
        mine = ensure_profile(db, user)
        mine.following_count = max(0, mine.following_count - 1)
        if target:
            theirs = ensure_profile(db, target)
            theirs.follower_count = max(0, theirs.follower_count - 1)
        write_audit(db, 'social.follow.removed', request, actor_user_id=user.id, resource_type='user', resource_id=user_id)
        db.commit()
    return APIMessage(message='User unfollowed')


@router.get('/profiles/{user_id}/followers', response_model=list[RelationshipRead])
def list_followers(user_id: str, limit: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail='User not found')
    rows = list(db.scalars(select(Follow).where(Follow.following_id == user_id).order_by(Follow.created_at.desc()).limit(limit)).all())
    return [relationship_read(db, edge.follower_id, followed_at=edge.created_at) for edge in rows if not is_blocked(db, user.id, edge.follower_id)]


@router.get('/profiles/{user_id}/following', response_model=list[RelationshipRead])
def list_following(user_id: str, limit: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail='User not found')
    rows = list(db.scalars(select(Follow).where(Follow.follower_id == user_id).order_by(Follow.created_at.desc()).limit(limit)).all())
    return [relationship_read(db, edge.following_id, followed_at=edge.created_at) for edge in rows if not is_blocked(db, user.id, edge.following_id)]


@router.post('/friends/requests/{user_id}', response_model=FriendRequestRead, status_code=201)
def send_friend_request(user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user_id == user.id:
        raise HTTPException(status_code=422, detail='You cannot send a friend request to yourself')
    target = db.get(User, user_id)
    if not target or target.status != 'active' or is_blocked(db, user.id, user_id):
        raise HTTPException(status_code=404, detail='User not found')
    if friendship_for(db, user.id, user_id):
        raise HTTPException(status_code=409, detail='Users are already friends')
    existing = db.scalar(
        select(FriendRequest).where(
            or_(
                (FriendRequest.requester_id == user.id) & (FriendRequest.addressee_id == user_id),
                (FriendRequest.requester_id == user_id) & (FriendRequest.addressee_id == user.id),
            ),
            FriendRequest.status == 'pending',
        )
    )
    if existing:
        raise HTTPException(status_code=409, detail='A pending friend request already exists')
    item = FriendRequest(requester_id=user.id, addressee_id=user_id)
    db.add(item)
    notify(db, user_id, 'social.friend_request', 'New friend request', f'{user.username} sent you a friend request.', {'request_id': item.id, 'user_id': user.id})
    write_audit(db, 'social.friend_request.created', request, actor_user_id=user.id, resource_type='friend_request', resource_id=item.id)
    db.commit()
    db.refresh(item)
    return {
        'id': item.id,
        'requester_id': item.requester_id,
        'addressee_id': item.addressee_id,
        'status': item.status,
        'requester_username': user.username,
        'requester_display_name': display_name(user, ensure_profile(db, user)),
        'created_at': item.created_at,
        'responded_at': item.responded_at,
    }


@router.get('/friends/requests/incoming', response_model=list[FriendRequestRead])
def incoming_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(FriendRequest).where(FriendRequest.addressee_id == user.id, FriendRequest.status == 'pending').order_by(FriendRequest.created_at.desc())).all())
    result = []
    for item in rows:
        requester = db.get(User, item.requester_id)
        result.append({
            'id': item.id,
            'requester_id': item.requester_id,
            'addressee_id': item.addressee_id,
            'status': item.status,
            'requester_username': requester.username if requester else None,
            'requester_display_name': display_name(requester, ensure_profile(db, requester)) if requester else None,
            'created_at': item.created_at,
            'responded_at': item.responded_at,
        })
    return result


@router.get('/friends/requests/outgoing', response_model=list[FriendRequestRead])
def outgoing_requests(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(FriendRequest).where(FriendRequest.requester_id == user.id, FriendRequest.status == 'pending').order_by(FriendRequest.created_at.desc())).all())
    return [
        {
            'id': item.id,
            'requester_id': item.requester_id,
            'addressee_id': item.addressee_id,
            'status': item.status,
            'created_at': item.created_at,
            'responded_at': item.responded_at,
        }
        for item in rows
    ]


@router.post('/friends/requests/{request_id}/accept', response_model=APIMessage)
def accept_friend_request(request_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(FriendRequest, request_id)
    if not item or item.addressee_id != user.id or item.status != 'pending':
        raise HTTPException(status_code=404, detail='Pending friend request not found')
    low, high = pair_ids(item.requester_id, item.addressee_id)
    db.add(Friendship(user_low_id=low, user_high_id=high))
    item.status = 'accepted'
    item.responded_at = utcnow()
    requester = db.get(User, item.requester_id)
    target = db.get(User, item.addressee_id)
    if requester and target:
        ensure_profile(db, requester).friend_count += 1
        ensure_profile(db, target).friend_count += 1
        for follower_id, following_id in ((requester.id, target.id), (target.id, requester.id)):
            if not db.scalar(select(Follow.id).where(Follow.follower_id == follower_id, Follow.following_id == following_id)):
                db.add(Follow(follower_id=follower_id, following_id=following_id))
                ensure_profile(db, db.get(User, follower_id)).following_count += 1
                ensure_profile(db, db.get(User, following_id)).follower_count += 1
        notify(db, requester.id, 'social.friend_accepted', 'Friend request accepted', f'{user.username} accepted your friend request.', {'user_id': user.id})
    write_audit(db, 'social.friend_request.accepted', request, actor_user_id=user.id, resource_type='friend_request', resource_id=item.id)
    db.commit()
    return APIMessage(message='Friend request accepted')


@router.post('/friends/requests/{request_id}/reject', response_model=APIMessage)
def reject_friend_request(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(FriendRequest, request_id)
    if not item or item.addressee_id != user.id or item.status != 'pending':
        raise HTTPException(status_code=404, detail='Pending friend request not found')
    item.status = 'rejected'
    item.responded_at = utcnow()
    db.commit()
    return APIMessage(message='Friend request rejected')


@router.delete('/friends/requests/{request_id}', response_model=APIMessage)
def cancel_friend_request(request_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item = db.get(FriendRequest, request_id)
    if not item or item.requester_id != user.id or item.status != 'pending':
        raise HTTPException(status_code=404, detail='Pending friend request not found')
    item.status = 'cancelled'
    item.responded_at = utcnow()
    db.commit()
    return APIMessage(message='Friend request cancelled')


@router.get('/friends', response_model=list[RelationshipRead])
def list_friends(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = list(db.scalars(select(Friendship).where(or_(Friendship.user_low_id == user.id, Friendship.user_high_id == user.id)).order_by(Friendship.created_at.desc())).all())
    return [relationship_read(db, row.user_high_id if row.user_low_id == user.id else row.user_low_id, friended_at=row.created_at) for row in rows]


@router.delete('/friends/{user_id}', response_model=APIMessage)
def remove_friend(user_id: str, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    relation = friendship_for(db, user.id, user_id)
    if relation:
        db.delete(relation)
        target = db.get(User, user_id)
        ensure_profile(db, user).friend_count = max(0, ensure_profile(db, user).friend_count - 1)
        if target:
            ensure_profile(db, target).friend_count = max(0, ensure_profile(db, target).friend_count - 1)
        write_audit(db, 'social.friendship.removed', request, actor_user_id=user.id, resource_type='user', resource_id=user_id)
        db.commit()
    return APIMessage(message='Friend removed')
