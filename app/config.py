from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Enterprise Social Media Platform API'
    app_version: str = '1.0.0'
    environment: Literal['development', 'test', 'staging', 'production'] = 'development'
    debug: bool = False
    api_prefix: str = '/api/v1'

    database_url: str = 'sqlite:///./social_media.db'
    secret_key: str = 'change-me-with-a-long-random-secret-at-least-32-characters'
    encryption_secret: str = 'change-me-with-a-different-long-random-secret'
    jwt_algorithm: str = 'HS256'
    access_token_minutes: int = 15
    refresh_token_days: int = 14
    mfa_ticket_minutes: int = 5
    email_verification_minutes: int = 60
    password_reset_minutes: int = 30
    email_change_minutes: int = 30

    password_min_length: int = 12
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    api_key_default_days: int = 365

    rate_limit_window_seconds: int = 60
    rate_limit_requests: int = 120
    login_rate_limit_requests: int = 20

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ['http://localhost:3000'])
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ['*'])

    redis_url: str | None = None
    frontend_url: str = 'http://localhost:3000'
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_email: str = 'no-reply@example.com'
    smtp_use_tls: bool = True

    expose_debug_tokens: bool = True
    docs_enabled: bool = True

    websocket_path: str = '/ws'
    websocket_heartbeat_seconds: int = 30
    websocket_idle_timeout_seconds: int = 120
    websocket_max_message_bytes: int = 65536
    max_message_length: int = 10000
    max_group_members: int = 500
    max_upload_bytes: int = 25 * 1024 * 1024
    upload_dir: str = './uploads'
    public_media_base_url: str = 'http://localhost:8000/media'
    presence_ttl_seconds: int = 90
    message_edit_window_minutes: int = 1440

    @field_validator('cors_origins', 'trusted_hosts', mode='before')
    @classmethod
    def parse_string_list(cls, value):
        if not isinstance(value, str):
            return value

        raw = value.strip()
        if not raw:
            return []

        # Accept both JSON arrays and convenient comma-separated values.
        if raw.startswith('['):
            parsed = json.loads(raw)
            if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
                raise ValueError('Expected a JSON array of strings')
            return [item.strip() for item in parsed if item.strip()]

        return [item.strip() for item in raw.split(',') if item.strip()]

    @field_validator('secret_key', 'encryption_secret')
    @classmethod
    def validate_secrets(cls, value: str):
        if len(value) < 32:
            raise ValueError('Security secrets must be at least 32 characters long')
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == 'production'


@lru_cache
def get_settings() -> Settings:
    return Settings()
