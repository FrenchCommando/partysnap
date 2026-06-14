import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import engine, get_session
from app.purge import purge_worker
from app.relay import relay_worker
from app.routers import admin, events, host, join, media, uploads

log = logging.getLogger("partysnap")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify the DB up front so a misconfig fails with a clear reason rather than
    # a cryptic 500 on the first request. (A wrong POSTGRES_PASSWORD still passes
    # the container healthcheck, which doesn't authenticate — but fails here.)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error(
            "Cannot connect to the database — check that POSTGRES_USER / "
            "POSTGRES_PASSWORD / POSTGRES_DB in .env match the initialized "
            "volume. Cause: %s",
            exc,
        )
        raise

    # Background workers: deletion purge always; Google relay only when convenience-capable.
    tasks = [asyncio.create_task(purge_worker())]
    if settings.google_configured:
        tasks.append(asyncio.create_task(relay_worker()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()


app = FastAPI(title="PartySnap API", lifespan=lifespan)
app.include_router(admin.router)
app.include_router(host.router)
app.include_router(events.router)
app.include_router(join.router)
app.include_router(media.router)
app.include_router(uploads.router)


@app.get("/api/health")
async def health(session: AsyncSession = Depends(get_session)) -> dict:
    """Liveness + DB connectivity (proves the FastAPI↔Postgres path)."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, f"database unavailable: {exc}"
        )
    return {"status": "ok"}
