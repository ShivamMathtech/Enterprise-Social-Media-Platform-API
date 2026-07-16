from __future__ import annotations

import os
from pathlib import Path

TEST_DB = Path(__file__).resolve().parents[1] / 'test_social_platform.db'
if TEST_DB.exists():
    TEST_DB.unlink()

os.environ['DATABASE_URL'] = f'sqlite:///{TEST_DB}'
os.environ['ENVIRONMENT'] = 'test'
os.environ['DEBUG'] = 'true'
os.environ['EXPOSE_DEBUG_TOKENS'] = 'true'
os.environ['SECRET_KEY'] = 'test-secret-key-that-is-definitely-longer-than-thirty-two-characters'
os.environ['ENCRYPTION_SECRET'] = 'test-encryption-key-that-is-definitely-longer-than-thirty-two-characters'
os.environ['RATE_LIMIT_REQUESTS'] = '5000'
os.environ['LOGIN_RATE_LIMIT_REQUESTS'] = '5000'
os.environ['REDIS_URL'] = ''
os.environ['UPLOAD_DIR'] = str(Path(__file__).resolve().parents[1] / 'test_uploads')
os.environ['PUBLIC_MEDIA_BASE_URL'] = 'http://testserver/media'

from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from scripts.seed import seed

Base.metadata.create_all(bind=engine)
seed()


def login(client: TestClient, email: str) -> dict:
    response = client.post('/api/v1/auth/login', json={'identifier': email, 'password': 'Password@123', 'device_name': 'pytest'})
    assert response.status_code == 200, response.text
    return response.json()['tokens']


def auth(token: str) -> dict[str, str]:
    return {'Authorization': f'Bearer {token}'}


def receive_type(websocket, expected: str, attempts: int = 10) -> dict:
    for _ in range(attempts):
        event = websocket.receive_json()
        if event.get('type') == expected:
            return event
    raise AssertionError(f'Did not receive event type {expected}')


def test_complete_realtime_chat_lifecycle():
    with TestClient(app, raise_server_exceptions=True) as client:
        alice_token = login(client, 'alice@social.example.com')['access_token']
        bob_token = login(client, 'bob@social.example.com')['access_token']
        moderator_token = login(client, 'moderator@social.example.com')['access_token']
        admin_token = login(client, 'admin@social.example.com')['access_token']

        users = client.get('/api/v1/chat/users?q=bob', headers=auth(alice_token))
        assert users.status_code == 200, users.text
        bob_id = users.json()[0]['id']

        direct = client.post(
            '/api/v1/chat/conversations',
            headers=auth(alice_token),
            json={'type': 'direct', 'participant_ids': [bob_id]},
        )
        assert direct.status_code == 201, direct.text
        conversation_id = direct.json()['id']

        with client.websocket_connect(f'/ws?token={bob_token}') as bob_ws:
            assert bob_ws.receive_json()['type'] == 'connection.ready'
            with client.websocket_connect(f'/ws?token={alice_token}') as alice_ws:
                assert alice_ws.receive_json()['type'] == 'connection.ready'
                alice_ws.send_json({
                    'type': 'message.send',
                    'request_id': 'client-1',
                    'conversation_id': conversation_id,
                    'data': {'content': 'Hello Bob from WebSocket!', 'type': 'text', 'client_message_id': 'alice-1'},
                })
                delivered = receive_type(bob_ws, 'message.created')
                assert delivered['data']['content'] == 'Hello Bob from WebSocket!'
                message_id = delivered['data']['id']

                bob_ws.send_json({
                    'type': 'typing.start',
                    'conversation_id': conversation_id,
                    'data': {},
                })
                typing = receive_type(alice_ws, 'typing.start')
                assert typing['data']['user_id'] == bob_id

                bob_ws.send_json({
                    'type': 'receipt.read',
                    'conversation_id': conversation_id,
                    'data': {'message_id': message_id},
                })
                read_event = receive_type(alice_ws, 'message.read')
                assert read_event['data']['message_id'] == message_id

        history = client.get(f'/api/v1/chat/conversations/{conversation_id}/messages', headers=auth(bob_token))
        assert history.status_code == 200, history.text
        assert any(item['content'] == 'Hello Bob from WebSocket!' for item in history.json()['items'])

        reaction = client.post(f'/api/v1/chat/messages/{message_id}/reactions', headers=auth(bob_token), json={'emoji': '🔥'})
        assert reaction.status_code == 200, reaction.text
        assert reaction.json()['reactions'][0]['count'] == 1

        edited = client.patch(f'/api/v1/chat/messages/{message_id}', headers=auth(alice_token), json={'content': 'Hello Bob — edited securely.'})
        assert edited.status_code == 200, edited.text
        assert edited.json()['status'] == 'edited'

        upload = client.post(
            '/api/v1/chat/uploads/presign',
            headers=auth(alice_token),
            json={'file_name': 'architecture.txt', 'mime_type': 'text/plain', 'size_bytes': 12},
        )
        assert upload.status_code == 201, upload.text
        upload_id = upload.json()['id']
        content = client.put(
            f'/api/v1/chat/uploads/{upload_id}/content',
            headers=auth(alice_token),
            files={'file': ('architecture.txt', b'hello world!', 'text/plain')},
        )
        assert content.status_code == 200, content.text
        attachment_message = client.post(
            f'/api/v1/chat/conversations/{conversation_id}/messages',
            headers=auth(alice_token),
            json={'type': 'file', 'content': 'Architecture file', 'attachment_ids': [upload_id], 'client_message_id': 'alice-file-1'},
        )
        assert attachment_message.status_code == 201, attachment_message.text
        assert attachment_message.json()['attachments'][0]['file_name'] == 'architecture.txt'

        report = client.post(
            f'/api/v1/chat/messages/{message_id}/report',
            headers=auth(bob_token),
            json={'reason': 'other', 'details': 'Lifecycle moderation test'},
        )
        assert report.status_code == 201, report.text
        resolved = client.patch(
            f"/api/v1/chat/moderation/reports/{report.json()['id']}",
            headers=auth(moderator_token),
            json={'status': 'resolved', 'resolution': 'Reviewed and retained', 'delete_message': False},
        )
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()['status'] == 'resolved'

        notifications = client.get('/api/v1/chat/notifications', headers=auth(bob_token))
        assert notifications.status_code == 200, notifications.text
        assert notifications.json()['total'] >= 1

        dashboard = client.get('/api/v1/chat/admin/dashboard', headers=auth(admin_token))
        assert dashboard.status_code == 200, dashboard.text
        assert dashboard.json()['messages_total'] >= 4


def test_group_members_invites_pins_blocks_and_moderation():
    with TestClient(app, raise_server_exceptions=True) as client:
        alice_token = login(client, 'alice@social.example.com')['access_token']
        bob_token = login(client, 'bob@social.example.com')['access_token']
        auditor_token = login(client, 'auditor@social.example.com')['access_token']
        moderator_token = login(client, 'moderator@social.example.com')['access_token']

        bob_id = client.get('/api/v1/chat/users?q=bob', headers=auth(alice_token)).json()[0]['id']
        group = client.post(
            '/api/v1/chat/conversations',
            headers=auth(alice_token),
            json={'type': 'group', 'title': 'Backend Engineering', 'participant_ids': [bob_id], 'is_private': True},
        )
        assert group.status_code == 201, group.text
        group_id = group.json()['id']

        invite = client.post(f'/api/v1/chat/conversations/{group_id}/invites', headers=auth(alice_token), json={'max_uses': 2, 'expires_in_hours': 24})
        assert invite.status_code == 201, invite.text
        accepted = client.post('/api/v1/chat/invites/accept', headers=auth(auditor_token), json={'token': invite.json()['invite_token']})
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()['member_count'] == 3

        message = client.post(
            f'/api/v1/chat/conversations/{group_id}/messages',
            headers=auth(alice_token),
            json={'content': 'Pinned engineering decision', 'type': 'text'},
        )
        assert message.status_code == 201, message.text
        message_id = message.json()['id']
        pin = client.post(f'/api/v1/chat/conversations/{group_id}/pins/{message_id}', headers=auth(alice_token))
        assert pin.status_code == 200, pin.text
        pins = client.get(f'/api/v1/chat/conversations/{group_id}/pins', headers=auth(bob_token))
        assert pins.status_code == 200 and len(pins.json()) == 1

        mute = client.post(
            f'/api/v1/chat/moderation/conversations/{group_id}/members/{bob_id}/mute',
            headers=auth(moderator_token),
            json={'duration_minutes': 10, 'reason': 'Automated test'},
        )
        assert mute.status_code == 200, mute.text
        blocked_send = client.post(f'/api/v1/chat/conversations/{group_id}/messages', headers=auth(bob_token), json={'content': 'Should fail while muted'})
        assert blocked_send.status_code == 403, blocked_send.text

        block = client.post(f'/api/v1/chat/blocks/{bob_id}', headers=auth(alice_token))
        assert block.status_code == 201, block.text
        direct = client.post('/api/v1/chat/conversations', headers=auth(alice_token), json={'type': 'direct', 'participant_ids': [bob_id]})
        assert direct.status_code == 403, direct.text
        unblocked = client.delete(f'/api/v1/chat/blocks/{bob_id}', headers=auth(alice_token))
        assert unblocked.status_code == 204


def test_security_and_idempotent_message_creation():
    with TestClient(app, raise_server_exceptions=True) as client:
        alice_token = login(client, 'alice@social.example.com')['access_token']
        bob_id = client.get('/api/v1/chat/users?q=bob', headers=auth(alice_token)).json()[0]['id']
        direct = client.post('/api/v1/chat/conversations', headers=auth(alice_token), json={'type': 'direct', 'participant_ids': [bob_id]})
        assert direct.status_code in {200, 201}
        conversation_id = direct.json()['id']
        payload = {'content': 'Exactly once from retrying client', 'type': 'text', 'client_message_id': 'retry-safe-123'}
        first = client.post(f'/api/v1/chat/conversations/{conversation_id}/messages', headers=auth(alice_token), json=payload)
        second = client.post(f'/api/v1/chat/conversations/{conversation_id}/messages', headers=auth(alice_token), json=payload)
        assert first.status_code == 201 and second.status_code == 201
        assert first.json()['id'] == second.json()['id']

        unauthenticated = client.get('/api/v1/chat/conversations')
        assert unauthenticated.status_code == 401
        invalid_ws_failed = False
        try:
            with client.websocket_connect('/ws?token=invalid-token'):
                pass
        except Exception:
            invalid_ws_failed = True
        assert invalid_ws_failed


def test_enterprise_social_media_lifecycle():
    with TestClient(app, raise_server_exceptions=True) as client:
        alice_token = login(client, 'alice@social.example.com')['access_token']
        bob_token = login(client, 'bob@social.example.com')['access_token']
        creator_token = login(client, 'creator@social.example.com')['access_token']
        moderator_token = login(client, 'moderator@social.example.com')['access_token']
        admin_token = login(client, 'admin@social.example.com')['access_token']

        alice_profile = client.get('/api/v1/social/profiles/me', headers=auth(alice_token))
        assert alice_profile.status_code == 200, alice_profile.text
        alice_id = alice_profile.json()['user_id']
        bob_profile = client.get('/api/v1/social/profiles/bob', headers=auth(alice_token))
        assert bob_profile.status_code == 200, bob_profile.text
        bob_id = bob_profile.json()['user_id']
        creator_profile = client.get('/api/v1/social/profiles/creator', headers=auth(alice_token))
        creator_id = creator_profile.json()['user_id']

        update = client.patch('/api/v1/social/profiles/me', headers=auth(alice_token), json={'bio': 'Backend engineer and social-platform tester.', 'city': 'Dehradun'})
        assert update.status_code == 200 and update.json()['city'] == 'Dehradun'

        request = client.post(f'/api/v1/social/friends/requests/{creator_id}', headers=auth(bob_token))
        assert request.status_code == 201, request.text
        accepted = client.post(f"/api/v1/social/friends/requests/{request.json()['id']}/accept", headers=auth(creator_token))
        assert accepted.status_code == 200, accepted.text

        page = client.post('/api/v1/social/pages', headers=auth(creator_token), json={
            'name': 'Enterprise API Academy',
            'slug': 'enterprise-api-academy',
            'category': 'Education',
            'description': 'Production backend engineering resources.'
        })
        assert page.status_code == 201, page.text
        page_id = page.json()['id']
        followed_page = client.post(f'/api/v1/social/pages/{page_id}/follow', headers=auth(alice_token))
        assert followed_page.status_code == 201

        group = client.post('/api/v1/social/groups', headers=auth(creator_token), json={
            'name': 'Distributed Systems Lab',
            'slug': 'distributed-systems-lab',
            'description': 'Enterprise architecture community.',
            'privacy': 'public',
            'approval_required': False
        })
        assert group.status_code == 201, group.text
        group_id = group.json()['id']
        joined = client.post(f'/api/v1/social/groups/{group_id}/join', headers=auth(alice_token))
        assert joined.status_code == 200 and joined.json()['membership_status'] == 'active'

        post = client.post('/api/v1/social/posts', headers=auth(creator_token), json={
            'page_id': page_id,
            'type': 'media',
            'text': 'Designing scalable social platforms with FastAPI. #FastAPI #SystemDesign',
            'visibility': 'public',
            'media': [{'media_type': 'image', 'url': 'https://example.com/social-architecture.png', 'alt_text': 'Social platform architecture'}],
            'hashtags': ['backend']
        })
        assert post.status_code == 201, post.text
        post_id = post.json()['id']
        assert set(post.json()['hashtags']) >= {'fastapi', 'systemdesign', 'backend'}

        reaction = client.put(f'/api/v1/social/posts/{post_id}/reaction', headers=auth(alice_token), json={'reaction': 'love'})
        assert reaction.status_code == 200 and reaction.json()['reaction_count'] == 1
        comment = client.post(f'/api/v1/social/posts/{post_id}/comments', headers=auth(alice_token), json={'text': 'Excellent architecture breakdown! @creator'})
        assert comment.status_code == 201, comment.text
        comment_id = comment.json()['id']
        reply = client.post(f'/api/v1/social/posts/{post_id}/comments', headers=auth(creator_token), json={'text': 'Thank you!', 'parent_id': comment_id})
        assert reply.status_code == 201
        save = client.put(f'/api/v1/social/posts/{post_id}/save', headers=auth(alice_token), json={'collection': 'architecture'})
        assert save.status_code == 200

        feed = client.get('/api/v1/social/posts/feed', headers=auth(alice_token))
        assert feed.status_code == 200, feed.text
        assert any(item['id'] == post_id for item in feed.json()['items'])
        saved = client.get('/api/v1/social/posts/saved?collection=architecture', headers=auth(alice_token))
        assert saved.status_code == 200 and saved.json()['items'][0]['id'] == post_id

        group_post = client.post('/api/v1/social/posts', headers=auth(alice_token), json={'group_id': group_id, 'text': 'Hello distributed systems community!', 'visibility': 'public'})
        assert group_post.status_code == 201, group_post.text
        group_posts = client.get(f'/api/v1/social/groups/{group_id}/posts', headers=auth(alice_token))
        assert group_posts.status_code == 200 and any(item['id'] == group_post.json()['id'] for item in group_posts.json()['items'])

        story = client.post('/api/v1/social/stories', headers=auth(creator_token), json={'media_type': 'image', 'media_url': 'https://example.com/story.png', 'caption': 'Shipping social features', 'visibility': 'public'})
        assert story.status_code == 201, story.text
        story_id = story.json()['id']
        viewed = client.get(f'/api/v1/social/stories/{story_id}', headers=auth(alice_token))
        assert viewed.status_code == 200 and viewed.json()['view_count'] == 1
        viewers = client.get(f'/api/v1/social/stories/{story_id}/viewers', headers=auth(creator_token))
        assert viewers.status_code == 200 and viewers.json()[0]['user_id'] == alice_id

        report = client.post('/api/v1/social/reports', headers=auth(bob_token), json={'resource_type': 'post', 'resource_id': post_id, 'reason': 'other', 'details': 'Moderation lifecycle validation'})
        assert report.status_code == 201, report.text
        resolved = client.patch(f"/api/v1/social/moderation/reports/{report.json()['id']}", headers=auth(moderator_token), json={'status': 'resolved', 'resolution': 'Reviewed and retained', 'remove_content': False})
        assert resolved.status_code == 200 and resolved.json()['status'] == 'resolved'

        creator_analytics = client.get('/api/v1/social/analytics/me', headers=auth(creator_token))
        assert creator_analytics.status_code == 200 and creator_analytics.json()['posts_total'] >= 2
        dashboard = client.get('/api/v1/social/analytics/dashboard', headers=auth(admin_token))
        assert dashboard.status_code == 200, dashboard.text
        assert dashboard.json()['posts_total'] >= 3
        trending = client.get('/api/v1/social/analytics/trending-hashtags', headers=auth(alice_token))
        assert trending.status_code == 200 and any(item['name'] == 'fastapi' for item in trending.json())
