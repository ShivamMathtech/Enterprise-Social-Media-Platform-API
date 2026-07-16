# Windows Setup

## Recommended runtime

Use Python 3.12. Python 3.13 may work, but production dependencies and deployment images are validated around Python 3.12.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

For a zero-dependency local database, edit `.env`:

```env
DATABASE_URL=sqlite:///./social_media.db
REDIS_URL=
TRUSTED_HOSTS=localhost,127.0.0.1,testserver
SECRET_KEY=replace-with-a-random-value-longer-than-32-characters
ENCRYPTION_SECRET=replace-with-a-different-value-longer-than-32-characters
```

Run:

```powershell
alembic upgrade head
python -m scripts.seed
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000/docs`.

## PostgreSQL and Redis

The simplest Windows option is Docker Desktop:

```powershell
docker compose up --build
```

## Common issue: CORS list parsing

Both formats are accepted:

```env
CORS_ORIGINS=http://localhost:3000,http://localhost:5173
```

or

```env
CORS_ORIGINS=["http://localhost:3000","http://localhost:5173"]
```
