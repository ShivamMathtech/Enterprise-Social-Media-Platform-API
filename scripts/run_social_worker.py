from __future__ import annotations

import time

from sqlalchemy import select

from app.database import SessionLocal
from app.models import SocialGroup, SocialPage, SocialPost, SocialProfile, Story, utcnow


def run_once() -> tuple[int, int]:
    now = utcnow()
    published = 0
    expired = 0
    with SessionLocal() as db:
        scheduled = list(db.scalars(select(SocialPost).where(SocialPost.status == 'scheduled', SocialPost.scheduled_at.is_not(None), SocialPost.scheduled_at <= now)).all())
        for post in scheduled:
            post.status = 'published'
            post.published_at = now
            profile = db.get(SocialProfile, post.author_id)
            if profile:
                profile.post_count += 1
            if post.page_id:
                page = db.get(SocialPage, post.page_id)
                if page:
                    page.post_count += 1
            if post.group_id:
                group = db.get(SocialGroup, post.group_id)
                if group:
                    group.post_count += 1
            if post.shared_post_id:
                shared = db.get(SocialPost, post.shared_post_id)
                if shared:
                    shared.share_count += 1
            published += 1

        stories = list(db.scalars(select(Story).where(Story.expires_at <= now, Story.deleted_at.is_(None))).all())
        for story in stories:
            story.deleted_at = now
            expired += 1
        db.commit()
    return published, expired


def main() -> None:
    print('Social worker started.')
    while True:
        published, expired = run_once()
        if published or expired:
            print(f'Published {published} scheduled posts; expired {expired} stories.')
        time.sleep(30)


if __name__ == '__main__':
    main()
