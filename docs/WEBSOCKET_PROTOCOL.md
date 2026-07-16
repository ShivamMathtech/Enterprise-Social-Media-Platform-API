# WebSocket Protocol

## Connection

```text
ws://localhost:8000/ws?token=<ACCESS_TOKEN>
```

The access token must be a valid JWT of type `access`. API keys are not accepted for WebSocket connections. The server closes unauthorized clients with code `4401` and inactive accounts with `4403`.

After connection, the server returns:

```json
{
  "type": "connection.ready",
  "data": {
    "user_id": "USER_UUID",
    "connection_id": "CONNECTION_UUID",
    "subscribed_conversation_ids": ["CONVERSATION_UUID"],
    "heartbeat_seconds": 30,
    "supported_events": ["message.send", "typing.start", "receipt.read"]
  }
}
```

Every client event follows this envelope:

```json
{
  "type": "event.name",
  "request_id": "optional-client-correlation-id",
  "conversation_id": "optional-conversation-id",
  "data": {}
}
```

## Client events

### `ping`

```json
{"type":"ping","request_id":"p-1","data":{}}
```

Server response: `pong`.

### `conversation.subscribe`

Membership is checked before subscription.

```json
{
  "type":"conversation.subscribe",
  "conversation_id":"CONVERSATION_UUID",
  "data":{}
}
```

### `conversation.unsubscribe`

Stops real-time delivery on this socket only. It does not remove membership.

### `message.send`

```json
{
  "type":"message.send",
  "request_id":"send-1001",
  "conversation_id":"CONVERSATION_UUID",
  "data":{
    "content":"Hello team",
    "type":"text",
    "client_message_id":"device-a-1001",
    "parent_id":null,
    "attachment_ids":[],
    "metadata":{"client":"web"}
  }
}
```

The `client_message_id` makes retries idempotent for the same sender and conversation. A first send broadcasts `message.created`; a replay receives `message.acknowledged` only on the retrying socket.

### `typing.start` / `typing.stop`

```json
{
  "type":"typing.start",
  "conversation_id":"CONVERSATION_UUID",
  "data":{}
}
```

Typing indicators are ephemeral and not stored.

### `receipt.read`

```json
{
  "type":"receipt.read",
  "conversation_id":"CONVERSATION_UUID",
  "data":{"message_id":"MESSAGE_UUID"}
}
```

Updates the member's conversation read position and creates/updates the message receipt.

### `reaction.add` / `reaction.remove`

```json
{
  "type":"reaction.add",
  "data":{"message_id":"MESSAGE_UUID","emoji":"🔥"}
}
```

A user may add each emoji once per message.

### `presence.update`

```json
{
  "type":"presence.update",
  "data":{"status":"away","custom_status":"In a meeting"}
}
```

Supported states: `online`, `away`, `dnd`, `offline`.

## Server events

- `connection.ready`
- `conversation.created`
- `conversation.updated`
- `conversation.archived`
- `conversation.deleted`
- `conversation.member_added`
- `conversation.member_joined`
- `conversation.member_left`
- `conversation.member_removed`
- `message.created`
- `message.acknowledged`
- `message.updated`
- `message.deleted`
- `message.pinned`
- `message.unpinned`
- `message.reaction_added`
- `message.reaction_removed`
- `message.read`
- `typing.start`
- `typing.stop`
- `presence.updated`
- `moderation.member_muted`
- `moderation.member_banned`
- `pong`
- `error`

## Error event

```json
{
  "type":"error",
  "request_id":"send-1001",
  "data":{
    "code":"event_processing_failed",
    "message":"Active conversation membership required"
  }
}
```

## Scaling

Without Redis, events are delivered to sockets connected to one API process. With `REDIS_URL`, each API replica publishes events to `enterprise-chat:events`; other replicas consume and deliver them locally. Use sticky sessions only as an optimization, not as a correctness requirement.
