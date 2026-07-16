# Client Integration Guide

## Authentication

1. Register or log in.
2. Store access tokens only in memory or secure platform storage.
3. Use the refresh token to obtain a new token pair before access expiry.
4. Replace the stored refresh token after every successful rotation.
5. If reuse is detected, the token family is revoked and the user must authenticate again.

## Feed pagination

Feed endpoints use `page` and `page_size`. Production clients should deduplicate posts by ID because ranking can change while a user scrolls. A future cursor-based feed can be added without changing post resources.

## Publishing

Create a post through `POST /api/v1/social/posts`. Media inputs are metadata URLs; production deployments should first upload bytes through the upload service or directly to object storage using presigned URLs.

## Real-time messaging

Connect to `ws://host/ws?token=<JWT>`. Wait for `connection.ready`, then send events such as `message.send`, `typing.start`, `receipt.read` and `presence.update`. See `WEBSOCKET_PROTOCOL.md`.

## Error envelope

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "details": [],
    "request_id": "..."
  }
}
```

Log the request ID in clients and support tools so server traces can be correlated.
