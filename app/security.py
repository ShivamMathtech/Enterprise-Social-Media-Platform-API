from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from cryptography.fernet import Fernet

from app.config import get_settings

settings = get_settings()
password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return password_hasher.check_needs_rehash(password_hash)
    except Exception:
        return True


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def secure_token(length: int = 48) -> str:
    return secrets.token_urlsafe(length)


def create_jwt(subject: str, token_type: str, expires_delta: timedelta, **claims: Any) -> str:
    now = utcnow()
    payload: dict[str, Any] = {
        'sub': subject,
        'type': token_type,
        'iat': int(now.timestamp()),
        'exp': int((now + expires_delta).timestamp()),
        'jti': claims.pop('jti', secrets.token_urlsafe(24)),
        'iss': settings.app_name,
        **claims,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_jwt(token: str, expected_type: str | None = None) -> dict[str, Any]:
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer=settings.app_name,
        options={'require': ['sub', 'type', 'iat', 'exp', 'jti']},
    )
    if expected_type and payload.get('type') != expected_type:
        raise jwt.InvalidTokenError('Unexpected token type')
    return payload


def create_access_token(user_id: str, roles: list[str], permissions: list[str], session_id: str | None = None) -> str:
    claims = {'roles': roles, 'permissions': permissions}
    if session_id:
        claims['sid'] = session_id
    return create_jwt(
        user_id,
        'access',
        timedelta(minutes=settings.access_token_minutes),
        **claims,
    )


def create_refresh_token(user_id: str, family_id: str, session_id: str, jti: str) -> str:
    return create_jwt(
        user_id,
        'refresh',
        timedelta(days=settings.refresh_token_days),
        family_id=family_id,
        sid=session_id,
        jti=jti,
    )


def create_mfa_ticket(user_id: str) -> str:
    return create_jwt(user_id, 'mfa_ticket', timedelta(minutes=settings.mfa_ticket_minutes))


def _fernet() -> Fernet:
    key = hashlib.sha256(settings.encryption_secret.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt_secret(value: str) -> str:
    return _fernet().decrypt(value.encode('utf-8')).decode('utf-8')


def generate_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii').rstrip('=')


def _base32_decode(secret: str) -> bytes:
    padding = '=' * ((8 - len(secret) % 8) % 8)
    return base64.b32decode(secret.upper() + padding)


def totp_at(secret: str, timestamp: int | None = None, interval: int = 30, digits: int = 6) -> str:
    timestamp = timestamp or int(time.time())
    counter = timestamp // interval
    digest = hmac.new(_base32_decode(secret), struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(secret: str, code: str, window: int = 1) -> bool:
    now = int(time.time())
    for step in range(-window, window + 1):
        expected = totp_at(secret, now + step * 30)
        if hmac.compare_digest(expected, code.strip()):
            return True
    return False


def otpauth_uri(secret: str, email: str) -> str:
    issuer = settings.app_name.replace(' ', '%20')
    label = f'{issuer}:{email}'.replace(' ', '%20')
    return f'otpauth://totp/{label}?secret={secret}&issuer={issuer}&algorithm=SHA1&digits=6&period=30'


def generate_backup_codes(count: int = 10) -> list[str]:
    return [f'{secrets.token_hex(4)[:4]}-{secrets.token_hex(4)[:4]}'.upper() for _ in range(count)]


def generate_api_key() -> tuple[str, str, str]:
    prefix = f'eak_{secrets.token_hex(4)}'
    secret = secrets.token_urlsafe(36)
    raw = f'{prefix}.{secret}'
    return raw, prefix, sha256(raw)
