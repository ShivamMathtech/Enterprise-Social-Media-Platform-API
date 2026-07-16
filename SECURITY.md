# Security and Production Readiness

This repository is an engineering starter, not a substitute for a security, privacy or legal review.

## Included controls

- Argon2 password hashing
- JWT access and rotating refresh tokens
- Refresh-token family revocation after reuse
- MFA and recovery codes
- Login lockout and rate limiting
- RBAC and scoped API keys
- Tenant membership checks
- Profile and content visibility rules
- User blocking and content reporting
- Request IDs, security headers and audit events
- Parameter validation and soft deletion
- Idempotent chat messages and durable outbox events

## Required production controls

- TLS at the edge and encrypted service-to-service traffic
- Managed secret storage and periodic key rotation
- Secure browser cookies or OS-protected mobile token storage
- Verified email/SMS delivery providers
- Object storage, content-type verification, malware scanning and image/video processing
- WAF, bot defense, abuse throttling and risk-based login controls
- Content moderation policy, appeals and evidence retention
- Data export, deletion, consent, age and regional privacy workflows
- Database backups, point-in-time recovery and disaster-recovery tests
- Centralized logs, metrics, traces and security alerting
- Dependency, container and infrastructure scanning
- Penetration testing before launch

Never use the demonstration credentials or development secrets in production.
