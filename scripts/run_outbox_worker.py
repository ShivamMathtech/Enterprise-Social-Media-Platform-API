from __future__ import annotations

import asyncio
import json
import os

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import EventOutbox, utcnow

settings = get_settings()


async def run() -> None:
    if not settings.redis_url:
        raise RuntimeError('REDIS_URL is required for the outbox worker')
    import redis.asyncio as redis

    client = redis.from_url(settings.redis_url, encoding='utf-8', decode_responses=True)
    channel = os.getenv('DOMAIN_EVENT_CHANNEL', 'enterprise-chat:domain-events')
    try:
        while True:
            with SessionLocal() as db:
                events = list(
                    db.scalars(
                        select(EventOutbox)
                        .where(EventOutbox.status == 'pending', EventOutbox.available_at <= utcnow())
                        .order_by(EventOutbox.created_at)
                        .limit(100)
                    ).all()
                )
                for event in events:
                    try:
                        await client.publish(
                            channel,
                            json.dumps(
                                {
                                    'event_id': event.id,
                                    'topic': event.topic,
                                    'aggregate_type': event.aggregate_type,
                                    'aggregate_id': event.aggregate_id,
                                    'payload': event.payload_json,
                                    'created_at': event.created_at.isoformat(),
                                }
                            ),
                        )
                        event.status = 'published'
                        event.published_at = utcnow()
                        event.attempts += 1
                    except Exception as exc:
                        event.attempts += 1
                        event.last_error = str(exc)[:2000]
                        if event.attempts >= 10:
                            event.status = 'failed'
                db.commit()
            await asyncio.sleep(1 if events else 3)
    finally:
        await client.aclose()


if __name__ == '__main__':
    asyncio.run(run())
