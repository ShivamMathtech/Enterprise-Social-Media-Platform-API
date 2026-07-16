# Architecture

## Bounded modules

1. **Identity and access** — users, credentials, MFA, sessions, API keys, organizations, roles and audit logs.
2. **Social graph** — profiles, follow edges, friend requests, friendships and blocks.
3. **Content** — posts, media, comments, reactions, hashtags, saved posts and stories.
4. **Communities** — pages, page administrators, groups and membership workflows.
5. **Feed and discovery** — network-aware home feed, explore ranking and trending hashtags.
6. **Moderation** — resource reports, enforcement, verification and audit evidence.
7. **Messaging** — conversations, messages, receipts, presence, uploads and WebSockets.
8. **Background processing** — scheduled publishing, story expiry and event-outbox delivery.

## Data consistency

Counters are updated within the same database transaction as their source relationship or content mutation. Unique constraints protect follow edges, friendships, reactions, saved posts, page followers, group memberships and idempotent message creation.

## Scale-out path

- Run stateless API replicas behind a load balancer.
- Use Redis pub/sub for cross-instance WebSocket distribution.
- Move media bytes to object storage and a CDN.
- Cache profile summaries, timelines and feed candidates in Redis.
- Partition high-volume post, reaction, notification and message tables.
- Publish domain events from the durable outbox to Kafka or another broker.
- Extract feed fan-out, media, notification, moderation and messaging into services as traffic requires.
