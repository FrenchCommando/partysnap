"""Host session exchange — API_CONTRACT §2. Gated host link + passcode -> session."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.auth import HOST_SESSION_TTL
from app.db import get_session
from app.security import hash_token, mint_session, verify_secret

router = APIRouter(prefix="/api/host", tags=["host"])


@router.post("/sessions", response_model=schemas.HostSession)
async def exchange(
    body: schemas.HostLogin, session: AsyncSession = Depends(get_session)
) -> schemas.HostSession:
    st = (
        await session.execute(
            select(models.ShareToken).where(
                models.ShareToken.token_hash == hash_token(body.token)
            )
        )
    ).scalar_one_or_none()
    valid = (
        st is not None
        and st.scope == "host"
        and st.revoked_at is None
        and (st.expires_at is None or st.expires_at > datetime.now(timezone.utc))
        and st.passcode_hash is not None
        and verify_secret(body.passcode, st.passcode_hash)
    )
    if not valid:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid host link or passcode")
    token = mint_session({"kind": "host", "event_id": str(st.event_id)}, HOST_SESSION_TTL)
    return schemas.HostSession(
        host_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=HOST_SESSION_TTL),
    )
