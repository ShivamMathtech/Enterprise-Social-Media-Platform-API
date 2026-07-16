from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import BackupCode, MFAMethod, User
from app.schemas import APIMessage, BackupCodesResponse, MFACodeRequest, MFADisableRequest, MFASetupResponse
from app.security import decrypt_secret, encrypt_secret, generate_totp_secret, otpauth_uri, verify_password, verify_totp
from app.services.audit_service import write_audit
from app.services.auth_service import replace_backup_codes, utcnow, verify_mfa_code

router = APIRouter(prefix='/mfa', tags=['Multi-Factor Authentication'])


@router.post('/totp/setup', response_model=MFASetupResponse)
def setup_totp(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    existing = db.scalar(select(MFAMethod).where(MFAMethod.user_id == user.id, MFAMethod.method == 'totp'))
    secret = generate_totp_secret()
    if existing:
        existing.secret_encrypted = encrypt_secret(secret)
        existing.confirmed_at = None
        existing.disabled_at = None
    else:
        db.add(MFAMethod(user_id=user.id, method='totp', secret_encrypted=encrypt_secret(secret)))
    write_audit(db, 'mfa.totp_setup_started', request, actor_user_id=user.id)
    db.commit()
    return MFASetupResponse(secret=secret, otpauth_uri=otpauth_uri(secret, user.email))


@router.post('/totp/confirm', response_model=BackupCodesResponse)
def confirm_totp(
    payload: MFACodeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    method = db.scalar(select(MFAMethod).where(MFAMethod.user_id == user.id, MFAMethod.method == 'totp'))
    if not method or not verify_totp(decrypt_secret(method.secret_encrypted), payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid TOTP code')
    method.confirmed_at = utcnow()
    method.disabled_at = None
    user.mfa_enabled = True
    codes = replace_backup_codes(db, user.id)
    write_audit(db, 'mfa.enabled', request, actor_user_id=user.id)
    db.commit()
    return BackupCodesResponse(backup_codes=codes)


@router.post('/totp/disable', response_model=APIMessage)
def disable_totp(
    payload: MFADisableRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Password is incorrect')
    if not verify_mfa_code(db, user, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid MFA code')
    method = db.scalar(select(MFAMethod).where(MFAMethod.user_id == user.id, MFAMethod.method == 'totp'))
    if method:
        method.disabled_at = utcnow()
    user.mfa_enabled = False
    db.query(BackupCode).filter(BackupCode.user_id == user.id).delete(synchronize_session=False)
    write_audit(db, 'mfa.disabled', request, actor_user_id=user.id)
    db.commit()
    return APIMessage(message='MFA disabled')


@router.post('/backup-codes/regenerate', response_model=BackupCodesResponse)
def regenerate_backup_codes(
    payload: MFACodeRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user.mfa_enabled or not verify_mfa_code(db, user, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Valid MFA code required')
    codes = replace_backup_codes(db, user.id)
    write_audit(db, 'mfa.backup_codes_regenerated', request, actor_user_id=user.id)
    db.commit()
    return BackupCodesResponse(backup_codes=codes)
