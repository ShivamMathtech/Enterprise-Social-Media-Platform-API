# Core Social Workflows

## Friend request

`requester -> pending request -> addressee accepts -> friendship row -> reciprocal follow edges -> notifications`

## Content publishing

`author validation -> page/group authorization -> post transaction -> media and hashtag links -> counters -> mentions -> audit -> feed visibility`

## Moderation

`user report -> moderation queue -> reviewing -> resolved/dismissed -> optional resource removal -> audit event`

## Scheduled publishing

Posts with a future `scheduled_at` are stored as `scheduled`. `scripts.run_social_worker` publishes eligible posts and updates profile/page/group/share counters.

## Story lifecycle

Stories remain visible until `expires_at`. The social worker marks expired stories deleted, while read paths also exclude expired records.
