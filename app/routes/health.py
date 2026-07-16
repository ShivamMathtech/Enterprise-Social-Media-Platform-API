from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db

router = APIRouter(tags=['System'])
settings = get_settings()


@router.get('/')
def root():
    return {
        'service': settings.app_name,
        'version': settings.app_version,
        'status': 'running',
        'docs': f'{settings.api_prefix.rsplit("/api/v1", 1)[0]}/docs' if settings.docs_enabled else None,
    }


@router.get('/health/live')
def live():
    return {'status': 'ok'}


@router.get('/health/ready')
def ready(db: Session = Depends(get_db)):
    db.execute(text('SELECT 1'))
    return {'status': 'ready', 'database': 'ok'}
