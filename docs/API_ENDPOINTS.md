# API Domains

All REST APIs are under `/api/v1`; the WebSocket gateway is `/ws`.

- `/auth` — registration, verification, login, refresh, recovery and profile security
- `/sessions` — active devices and session revocation
- `/mfa` — TOTP setup, confirmation, backup codes and disablement
- `/organizations` — multi-tenant organizations and invitations
- `/rbac` — roles, permissions and assignments
- `/api-keys` — scoped machine credentials
- `/admin` and `/audit` — account administration and audit history
- `/social/profiles`, `/social/people`, `/social/friends` — social graph and discovery
- `/social/posts`, `/social/comments`, `/social/stories` — publishing and interaction
- `/social/pages`, `/social/groups` — creators and communities
- `/social/reports`, `/social/moderation` — safety and enforcement
- `/social/analytics` — creator, platform and trending metrics
- `/chat` — direct/group messaging, uploads, presence, notifications and chat moderation
- `/health` — liveness and readiness

Use the generated `openapi.json`, Swagger UI or Postman collection for complete request and response contracts.
