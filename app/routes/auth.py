from __future__ import annotations

from datetime import timedelta

import jwt
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session
from fastapi.security import OAuth2PasswordRequestForm

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import OneTimeToken, RefreshSession, Role, User, UserRole
from app.schemas import (
    APIMessage,
    ChangePasswordRequest,
    EmailChangeRequest,
    EmailChangeResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    LoginResponse,
    MFALoginVerify,
    RefreshRequest,
    RegistrationResponse,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenPair,
    UserProfileUpdate,
    UserRead,
    UserRegister,
    VerifyTokenRequest,
)
from app.security import create_mfa_ticket, decode_jwt, hash_password, verify_password
from app.services.audit_service import write_audit
from app.services.email_service import send_email_change_confirmation, send_password_reset_email, send_verification_email
from app.services.auth_service import (
    consume_one_time_token,
    create_one_time_token,
    get_user_by_identifier,
    issue_token_pair,
    rotate_refresh_token,
    user_to_read,
    validate_login_user,
    verify_mfa_code,
    utcnow,
)

router = APIRouter(prefix='/auth', tags=['Authentication'])
settings = get_settings()


@router.post('/register', response_model=RegistrationResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    existing = db.scalar(
        select(User).where(or_(User.email == str(payload.email).lower(), User.username == payload.username.lower()))
    )
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email or username is already registered')

    user = User(
        email=str(payload.email).lower(),
        username=payload.username.lower(),
        password_hash=hash_password(payload.password),
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone=payload.phone,
    )
    db.add(user)
    db.flush()
    default_role = db.scalar(select(Role).where(Role.name == 'member'))
    if default_role:
        db.add(UserRole(user_id=user.id, role_id=default_role.id))
    token = create_one_time_token(db, user.id, 'email_verification', settings.email_verification_minutes)
    background_tasks.add_task(send_verification_email, user.email, token)
    write_audit(db, 'auth.user_registered', request, actor_user_id=user.id, resource_type='user', resource_id=user.id)
    db.commit()
    db.refresh(user)
    return RegistrationResponse(
        user=user_to_read(db, user),
        debug_verification_token=token if settings.expose_debug_tokens and not settings.is_production else None,
    )


@router.post('/verify-email', response_model=APIMessage)
def verify_email(payload: VerifyTokenRequest, request: Request, db: Session = Depends(get_db)):
    token = consume_one_time_token(db, payload.token, 'email_verification')
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    user.email_verified = True
    user.status = 'active'
    write_audit(db, 'auth.email_verified', request, actor_user_id=user.id, resource_type='user', resource_id=user.id)
    db.commit()
    return APIMessage(message='Email verified successfully')


@router.post('/resend-verification', response_model=ForgotPasswordResponse)
def resend_verification(payload: ResendVerificationRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    debug_token = None
    if user and not user.email_verified and user.status != 'deleted':
        db.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == 'email_verification',
                OneTimeToken.used_at.is_(None),
            )
            .values(used_at=utcnow())
        )
        token = create_one_time_token(db, user.id, 'email_verification', settings.email_verification_minutes)
        background_tasks.add_task(send_verification_email, user.email, token)
        debug_token = token if settings.expose_debug_tokens and not settings.is_production else None
        write_audit(db, 'auth.verification_resent', request, actor_user_id=user.id)
        db.commit()
    return ForgotPasswordResponse(
        message='If the account is eligible, a verification message has been issued.',
        debug_reset_token=debug_token,
    )


@router.post('/login', response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = validate_login_user(db, payload.identifier, payload.password, request)
    if user.mfa_enabled:
        ticket = create_mfa_ticket(user.id)
        write_audit(db, 'auth.mfa_challenge_created', request, actor_user_id=user.id)
        db.commit()
        return LoginResponse(mfa_required=True, mfa_ticket=ticket, user=user_to_read(db, user))
    tokens = issue_token_pair(db, user, request, payload.device_name)
    return LoginResponse(mfa_required=False, tokens=tokens, user=user_to_read(db, user))


@router.post('/token', response_model=LoginResponse, summary='OAuth2-compatible password token endpoint')
def oauth2_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = validate_login_user(db, form_data.username, form_data.password, request)
    if user.mfa_enabled:
        ticket = create_mfa_ticket(user.id)
        write_audit(db, 'auth.mfa_challenge_created', request, actor_user_id=user.id)
        db.commit()
        return LoginResponse(mfa_required=True, mfa_ticket=ticket, user=user_to_read(db, user))
    tokens = issue_token_pair(db, user, request, device_name='OAuth2 client')
    return LoginResponse(mfa_required=False, tokens=tokens, user=user_to_read(db, user))


@router.post('/mfa/verify-login', response_model=LoginResponse)
def verify_login_mfa(payload: MFALoginVerify, request: Request, db: Session = Depends(get_db)):
    try:
        claims = decode_jwt(payload.mfa_ticket, expected_type='mfa_ticket')
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired MFA ticket')
    user = db.get(User, claims.get('sub'))
    if not user or not user.mfa_enabled or user.status != 'active':
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='MFA challenge is not valid')
    if not verify_mfa_code(db, user, payload.code):
        write_audit(db, 'auth.mfa_failed', request, actor_user_id=user.id, outcome='failure')
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid MFA code')
    write_audit(db, 'auth.mfa_succeeded', request, actor_user_id=user.id)
    db.commit()
    tokens = issue_token_pair(db, user, request, payload.device_name)
    return LoginResponse(mfa_required=False, tokens=tokens, user=user_to_read(db, user))


@router.post('/refresh', response_model=TokenPair)
def refresh(payload: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    try:
        claims = decode_jwt(payload.refresh_token, expected_type='refresh')
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid or expired refresh token')
    return rotate_refresh_token(db, claims, request)


@router.post('/logout', response_model=APIMessage)
def logout(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session_id = getattr(request.state, 'session_id', None)
    if session_id:
        session = db.get(RefreshSession, session_id)
        if session and session.user_id == user.id and session.revoked_at is None:
            session.revoked_at = utcnow()
            session.revoke_reason = 'user_logout'
    write_audit(db, 'auth.logout', request, actor_user_id=user.id, resource_type='session', resource_id=session_id)
    db.commit()
    return APIMessage(message='Logged out successfully')


@router.post('/logout-all', response_model=APIMessage)
def logout_all(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason='logout_all')
    )
    write_audit(db, 'auth.logout_all', request, actor_user_id=user.id)
    db.commit()
    return APIMessage(message='All sessions have been revoked')


@router.post('/password/forgot', response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == str(payload.email).lower()))
    debug_token = None
    if user and user.status not in {'deleted', 'disabled'}:
        db.execute(
            update(OneTimeToken)
            .where(
                OneTimeToken.user_id == user.id,
                OneTimeToken.purpose == 'password_reset',
                OneTimeToken.used_at.is_(None),
            )
            .values(used_at=utcnow())
        )
        token = create_one_time_token(db, user.id, 'password_reset', settings.password_reset_minutes)
        background_tasks.add_task(send_password_reset_email, user.email, token)
        debug_token = token if settings.expose_debug_tokens and not settings.is_production else None
        write_audit(db, 'auth.password_reset_requested', request, actor_user_id=user.id)
        db.commit()
    return ForgotPasswordResponse(
        message='If the email is registered, password reset instructions have been issued.',
        debug_reset_token=debug_token,
    )


@router.post('/password/reset', response_model=APIMessage)
def reset_password(payload: ResetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    token = consume_one_time_token(db, payload.token, 'password_reset')
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='User not found')
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    if user.email_verified:
        user.status = 'active'
    db.execute(
        update(RefreshSession)
        .where(RefreshSession.user_id == user.id, RefreshSession.revoked_at.is_(None))
        .values(revoked_at=utcnow(), revoke_reason='password_reset')
    )
    write_audit(db, 'auth.password_reset_completed', request, actor_user_id=user.id)
    db.commit()
    return APIMessage(message='Password reset successfully')


@router.post('/password/change', response_model=APIMessage)
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.current_password, user.password_hash):
        write_audit(db, 'auth.password_change_failed', request, actor_user_id=user.id, outcome='failure')
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Current password is incorrect')
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='New password must be different')
    user.password_hash = hash_password(payload.new_password)
    user.password_changed_at = utcnow()
    current_session = getattr(request.state, 'session_id', None)
    db.execute(
        update(RefreshSession)
        .where(
            RefreshSession.user_id == user.id,
            RefreshSession.revoked_at.is_(None),
            RefreshSession.id != current_session,
        )
        .values(revoked_at=utcnow(), revoke_reason='password_changed')
    )
    write_audit(db, 'auth.password_changed', request, actor_user_id=user.id)
    db.commit()
    return APIMessage(message='Password changed; other sessions were revoked')


@router.get('/me', response_model=UserRead)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_to_read(db, user)


@router.patch('/me', response_model=UserRead)
def update_me(
    payload: UserProfileUpdate,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, key, value)
    write_audit(db, 'auth.profile_updated', request, actor_user_id=user.id, resource_type='user', resource_id=user.id)
    db.commit()
    db.refresh(user)
    return user_to_read(db, user)


@router.post('/email/change/request', response_model=EmailChangeResponse)
def request_email_change(
    payload: EmailChangeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    new_email = str(payload.new_email).lower()
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Password is incorrect')
    if db.scalar(select(User).where(User.email == new_email, User.id != user.id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email is already in use')
    token = create_one_time_token(
        db,
        user.id,
        'email_change',
        settings.email_change_minutes,
        payload={'new_email': new_email},
    )
    background_tasks.add_task(send_email_change_confirmation, new_email, token)
    write_audit(db, 'auth.email_change_requested', request, actor_user_id=user.id, metadata={'new_email': new_email})
    db.commit()
    return EmailChangeResponse(
        message='Email change confirmation issued',
        debug_confirmation_token=token if settings.expose_debug_tokens and not settings.is_production else None,
    )


@router.post('/email/change/confirm', response_model=UserRead)
def confirm_email_change(
    payload: VerifyTokenRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    token = consume_one_time_token(db, payload.token, 'email_change')
    if token.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Token belongs to another user')
    new_email = (token.payload or {}).get('new_email')
    if not new_email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid email change token')
    if db.scalar(select(User).where(User.email == new_email, User.id != user.id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Email is already in use')
    old_email = user.email
    user.email = new_email
    user.email_verified = True
    write_audit(db, 'auth.email_changed', request, actor_user_id=user.id, metadata={'old_email': old_email, 'new_email': new_email})
    db.commit()
    db.refresh(user)
    return user_to_read(db, user)
