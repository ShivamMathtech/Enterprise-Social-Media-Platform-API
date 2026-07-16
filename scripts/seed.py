from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.database import SessionLocal
from app.models import (
    Conversation,
    ConversationMember,
    Follow,
    Friendship,
    GroupMember,
    Hashtag,
    Message,
    MessageReceipt,
    PageAdmin,
    PageFollow,
    Permission,
    PostComment,
    PostHashtag,
    PostMedia,
    PostReaction,
    Role,
    RolePermission,
    SocialGroup,
    SocialPage,
    SocialPost,
    SocialProfile,
    Story,
    User,
    UserRole,
    utcnow,
)
from app.security import hash_password

PERMISSIONS = {
    '*': ('Full platform access', 'Bypasses individual permission checks.'),
    'users.read': ('Read users', 'View user accounts and profile metadata.'),
    'users.write': ('Manage users', 'Lock, unlock, disable and restore users.'),
    'users.delete': ('Delete users', 'Soft-delete user accounts.'),
    'rbac.read': ('Read RBAC', 'View roles and permissions.'),
    'rbac.write': ('Manage RBAC', 'Create, edit and delete roles.'),
    'rbac.assign': ('Assign roles', 'Assign global roles to users.'),
    'audit.read': ('Read audit logs', 'View security and administration audit events.'),
    'chat.moderate': ('Moderate chat', 'Review reports and mute or ban conversation members.'),
    'chat.admin': ('Administer chat', 'View platform-wide chat analytics.'),
    'social.moderate': ('Moderate social content', 'Review reports, remove content and verify profiles.'),
    'social.admin': ('Administer social platform', 'View platform analytics and manage social operations.'),
}

ROLE_DEFINITIONS = {
    'super_admin': {'description': 'Unrestricted platform administrator.', 'scope': 'global', 'permissions': ['*']},
    'security_admin': {'description': 'Manages accounts, access controls and security investigations.', 'scope': 'global', 'permissions': ['users.read', 'users.write', 'rbac.read', 'rbac.write', 'rbac.assign', 'audit.read']},
    'social_moderator': {'description': 'Reviews social content reports and applies platform enforcement.', 'scope': 'global', 'permissions': ['users.read', 'social.moderate', 'social.admin', 'chat.moderate', 'chat.admin', 'audit.read']},
    'auditor': {'description': 'Read-only access to users, access controls and audit events.', 'scope': 'global', 'permissions': ['users.read', 'rbac.read', 'audit.read']},
    'member': {'description': 'Default authenticated social-media user.', 'scope': 'global', 'permissions': []},
    'organization_owner': {'description': 'Full control of one organization.', 'scope': 'organization', 'permissions': []},
    'organization_admin': {'description': 'Manages organization profile, members and invitations.', 'scope': 'organization', 'permissions': []},
    'organization_member': {'description': 'Standard member of an organization.', 'scope': 'organization', 'permissions': []},
}

DEMO_USERS = [
    ('admin@social.example.com', 'socialadmin', 'Platform', 'Administrator', 'super_admin'),
    ('moderator@social.example.com', 'socialmod', 'Social', 'Moderator', 'social_moderator'),
    ('auditor@social.example.com', 'socialauditor', 'Security', 'Auditor', 'auditor'),
    ('creator@social.example.com', 'creator', 'Aarav', 'Creator', 'member'),
    ('alice@social.example.com', 'alice', 'Alice', 'Sharma', 'member'),
    ('bob@social.example.com', 'bob', 'Bob', 'Singh', 'member'),
]


def seed() -> None:
    with SessionLocal() as db:
        permission_map: dict[str, Permission] = {}
        for code, (name, description) in PERMISSIONS.items():
            permission = db.scalar(select(Permission).where(Permission.code == code))
            if not permission:
                permission = Permission(code=code, name=name, description=description)
                db.add(permission)
                db.flush()
            permission_map[code] = permission

        roles: dict[str, Role] = {}
        for name, definition in ROLE_DEFINITIONS.items():
            role = db.scalar(select(Role).where(Role.name == name))
            if not role:
                role = Role(name=name, description=definition['description'], scope=definition['scope'], is_system=True)
                db.add(role)
                db.flush()
            roles[name] = role
            existing_ids = set(db.scalars(select(RolePermission.permission_id).where(RolePermission.role_id == role.id)).all())
            for code in definition['permissions']:
                permission = permission_map[code]
                if permission.id not in existing_ids:
                    db.add(RolePermission(role_id=role.id, permission_id=permission.id))

        users: dict[str, User] = {}
        for email, username, first_name, last_name, role_name in DEMO_USERS:
            demo_user = db.scalar(select(User).where(User.email == email))
            if not demo_user:
                demo_user = User(email=email, username=username, password_hash=hash_password('Password@123'), first_name=first_name, last_name=last_name, status='active', email_verified=True)
                db.add(demo_user)
                db.flush()
            users[username] = demo_user
            role = roles[role_name]
            if not db.scalar(select(UserRole).where(UserRole.user_id == demo_user.id, UserRole.role_id == role.id)):
                db.add(UserRole(user_id=demo_user.id, role_id=role.id, assigned_by=users.get('socialadmin', demo_user).id))
            if not db.get(SocialProfile, demo_user.id):
                db.add(SocialProfile(
                    user_id=demo_user.id,
                    display_name=f'{first_name} {last_name}',
                    bio='Enterprise social-media demo account.',
                    city='Dehradun' if username in {'creator', 'alice'} else 'Delhi',
                    country='India',
                    profile_visibility='public',
                    is_verified=username in {'socialadmin', 'creator'},
                ))

        db.flush()
        low, high = sorted((users['alice'].id, users['bob'].id))
        if not db.scalar(select(Friendship).where(Friendship.user_low_id == low, Friendship.user_high_id == high)):
            db.add(Friendship(user_low_id=low, user_high_id=high))
            db.get(SocialProfile, users['alice'].id).friend_count += 1
            db.get(SocialProfile, users['bob'].id).friend_count += 1
        for follower, following in [('alice', 'creator'), ('bob', 'creator'), ('alice', 'bob'), ('bob', 'alice')]:
            if not db.scalar(select(Follow).where(Follow.follower_id == users[follower].id, Follow.following_id == users[following].id)):
                db.add(Follow(follower_id=users[follower].id, following_id=users[following].id))
                db.get(SocialProfile, users[follower].id).following_count += 1
                db.get(SocialProfile, users[following].id).follower_count += 1

        page = db.scalar(select(SocialPage).where(SocialPage.slug == 'mathtech-developers'))
        if not page:
            page = SocialPage(owner_id=users['creator'].id, name='MathTech Developers', slug='mathtech-developers', category='Technology', description='A demonstration creator page for backend engineering and system design.', is_verified=True)
            db.add(page)
            db.flush()
            db.add(PageAdmin(page_id=page.id, user_id=users['creator'].id, role='admin'))
            for username in ('alice', 'bob'):
                db.add(PageFollow(page_id=page.id, user_id=users[username].id))
                page.follower_count += 1

        group = db.scalar(select(SocialGroup).where(SocialGroup.slug == 'fastapi-builders'))
        if not group:
            group = SocialGroup(owner_id=users['creator'].id, name='FastAPI Builders', slug='fastapi-builders', description='Community for developers building production APIs.', privacy='public', member_count=3)
            db.add(group)
            db.flush()
            db.add_all([
                GroupMember(group_id=group.id, user_id=users['creator'].id, role='owner', status='active'),
                GroupMember(group_id=group.id, user_id=users['alice'].id, role='moderator', status='active'),
                GroupMember(group_id=group.id, user_id=users['bob'].id, role='member', status='active'),
            ])

        post = db.scalar(select(SocialPost).where(SocialPost.text.like('Welcome to the Enterprise Social Media API%')))
        if not post:
            post = SocialPost(author_id=users['creator'].id, page_id=page.id, type='media', text='Welcome to the Enterprise Social Media API! #FastAPI #BackendEngineering', visibility='public')
            db.add(post)
            db.flush()
            db.add(PostMedia(post_id=post.id, media_type='image', url='https://example.com/media/social-api-cover.png', alt_text='Enterprise Social Media API cover'))
            for tag_name in ('fastapi', 'backendengineering'):
                tag = db.scalar(select(Hashtag).where(Hashtag.name == tag_name))
                if not tag:
                    tag = Hashtag(name=tag_name, post_count=0)
                    db.add(tag)
                    db.flush()
                tag.post_count += 1
                db.add(PostHashtag(post_id=post.id, hashtag_id=tag.id))
            db.add(PostReaction(post_id=post.id, user_id=users['alice'].id, reaction='love'))
            post.reaction_count = 1
            comment = PostComment(post_id=post.id, author_id=users['bob'].id, text='This is a strong enterprise starter!')
            db.add(comment)
            post.comment_count = 1
            page.post_count += 1
            db.get(SocialProfile, users['creator'].id).post_count += 1

        if not db.scalar(select(Story).where(Story.author_id == users['creator'].id, Story.expires_at > utcnow())):
            db.add(Story(author_id=users['creator'].id, media_type='image', media_url='https://example.com/media/story.png', caption='Building the next scalable API.', visibility='public', expires_at=utcnow() + timedelta(hours=24)))

        demo = db.scalar(select(Conversation).where(Conversation.title == 'Social Platform Demo Chat'))
        if not demo:
            demo = Conversation(type='group', title='Social Platform Demo Chat', description='Seeded private messaging workspace.', created_by=users['socialadmin'].id, is_private=False, max_members=500, next_sequence=2, last_message_at=utcnow())
            db.add(demo)
            db.flush()
            for username, role in [('socialadmin', 'owner'), ('socialmod', 'moderator'), ('alice', 'member'), ('bob', 'member')]:
                db.add(ConversationMember(conversation_id=demo.id, user_id=users[username].id, role=role, invited_by=users['socialadmin'].id))
            first = Message(conversation_id=demo.id, sender_id=users['socialadmin'].id, sequence=1, type='system', content='Welcome to the enterprise social platform messaging demo.')
            second = Message(conversation_id=demo.id, sender_id=users['alice'].id, sequence=2, type='text', content='Private messaging and social networking are connected.')
            db.add_all([first, second])
            db.flush()
            db.add_all([
                MessageReceipt(message_id=second.id, user_id=users['alice'].id, delivered_at=utcnow(), read_at=utcnow()),
                MessageReceipt(message_id=second.id, user_id=users['bob'].id, delivered_at=utcnow()),
            ])

        db.commit()
        print('Seed completed.')
        print('Password for all demo users: Password@123')
        for email, _, _, _, role_name in DEMO_USERS:
            print(f'{role_name}: {email}')


if __name__ == '__main__':
    seed()
