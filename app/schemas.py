from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.config import get_settings

settings = get_settings()


class APIMessage(BaseModel):
    message: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class UserRegister(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')
    password: str = Field(min_length=settings.password_min_length, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)

    @field_validator('email')
    @classmethod
    def normalize_email(cls, value: EmailStr):
        return str(value).lower()

    @field_validator('username')
    @classmethod
    def normalize_username(cls, value: str):
        return value.lower()

    @field_validator('password')
    @classmethod
    def strong_password(cls, value: str):
        categories = [bool(re.search(pattern, value)) for pattern in [r'[a-z]', r'[A-Z]', r'\d', r'[^A-Za-z0-9]']]
        if sum(categories) < 3:
            raise ValueError('Password must contain at least three of: lowercase, uppercase, number, special character')
        return value


class UserProfileUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=32)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    username: str
    first_name: str
    last_name: str
    phone: str | None
    status: str
    email_verified: bool
    phone_verified: bool
    mfa_enabled: bool
    last_login_at: datetime | None
    created_at: datetime
    roles: list[str] = []
    permissions: list[str] = []


class RegistrationResponse(BaseModel):
    user: UserRead
    verification_required: bool = True
    debug_verification_token: str | None = None


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = 'bearer'
    access_expires_in: int
    refresh_expires_in: int


class LoginRequest(BaseModel):
    identifier: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)
    device_name: str | None = Field(default=None, max_length=160)


class LoginResponse(BaseModel):
    mfa_required: bool
    mfa_ticket: str | None = None
    tokens: TokenPair | None = None
    user: UserRead | None = None


class MFALoginVerify(BaseModel):
    mfa_ticket: str
    code: str = Field(min_length=6, max_length=20)
    device_name: str | None = Field(default=None, max_length=160)


class RefreshRequest(BaseModel):
    refresh_token: str


class VerifyTokenRequest(BaseModel):
    token: str = Field(min_length=16)


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    debug_reset_token: str | None = None


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=settings.password_min_length, max_length=128)

    @field_validator('new_password')
    @classmethod
    def strong_password(cls, value: str):
        return UserRegister.strong_password(value)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=settings.password_min_length, max_length=128)

    @field_validator('new_password')
    @classmethod
    def strong_password(cls, value: str):
        return UserRegister.strong_password(value)


class EmailChangeRequest(BaseModel):
    new_email: EmailStr
    password: str


class EmailChangeResponse(BaseModel):
    message: str
    debug_confirmation_token: str | None = None


class MFASetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class MFACodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class MFADisableRequest(BaseModel):
    password: str
    code: str = Field(min_length=6, max_length=20)


class BackupCodesResponse(BaseModel):
    backup_codes: list[str]
    warning: str = 'Store these codes securely. Each code can be used only once.'


class SessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    ip_address: str | None
    user_agent: str | None
    device_name: str | None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    current: bool = False


class PermissionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    code: str
    name: str
    description: str | None


class RoleCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80, pattern=r'^[a-z][a-z0-9_]*$')
    description: str | None = Field(default=None, max_length=500)
    scope: Literal['global', 'organization'] = 'global'
    permission_codes: list[str] = []


class RoleUpdate(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    permission_codes: list[str] | None = None


class RoleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    description: str | None
    scope: str
    is_system: bool
    permissions: list[str] = []
    created_at: datetime


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    slug: str = Field(min_length=2, max_length=100, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    status: Literal['active', 'suspended'] | None = None


class OrganizationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    slug: str
    status: str
    created_by: str
    created_at: datetime
    member_count: int = 0
    my_role: str | None = None


class InvitationCreate(BaseModel):
    email: EmailStr
    role_id: str
    expires_in_hours: int = Field(default=72, ge=1, le=720)


class InvitationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    email: EmailStr
    role_id: str
    status: str
    expires_at: datetime
    accepted_at: datetime | None
    created_at: datetime
    debug_invitation_token: str | None = None


class InvitationAccept(BaseModel):
    token: str


class MembershipRead(BaseModel):
    id: str
    organization_id: str
    user_id: str
    email: EmailStr
    username: str
    role_id: str
    role_name: str
    status: str
    joined_at: datetime


class MembershipUpdate(BaseModel):
    role_id: str | None = None
    status: Literal['active', 'suspended'] | None = None


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    organization_id: str | None = None
    scopes: list[str] = []
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    prefix: str
    organization_id: str | None
    scopes: list[str]
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(BaseModel):
    api_key: str
    record: ApiKeyRead
    warning: str = 'Copy this API key now. It will not be shown again.'


class UserAdminUpdate(BaseModel):
    status: Literal['pending_verification', 'active', 'locked', 'disabled']


class AuditLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    actor_user_id: str | None
    organization_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    outcome: str
    ip_address: str | None
    user_agent: str | None
    request_id: str | None
    metadata_json: dict | None
    created_at: datetime


class PaginatedUsers(BaseModel):
    items: list[UserRead]
    total: int
    page: int
    page_size: int


class PaginatedAuditLogs(BaseModel):
    items: list[AuditLogRead]
    total: int
    page: int
    page_size: int
