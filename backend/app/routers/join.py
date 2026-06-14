"""Guest join — API_CONTRACT §6. Contribute link + name -> device token."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, schemas
from app.db import get_session
from app.security import generate_token, hash_token

router = APIRouter(prefix="/api", tags=["join"])


@router.post("/join", response_model=schemas.JoinResponse)
async def join(
    body: schemas.JoinRequest, session: AsyncSession = Depends(get_session)
) -> schemas.JoinResponse:
    st = (
        await session.execute(
            select(models.ShareToken).where(
                models.ShareToken.token_hash == hash_token(body.share_token)
            )
        )
    ).scalar_one_or_none()
    valid = (
        st is not None
        and st.scope == "contribute"
        and st.revoked_at is None
        and (st.expires_at is None or st.expires_at > datetime.now(timezone.utc))
    )
    if not valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or expired contribute link"
        )

    event = await session.get(models.Event, st.event_id)
    if event is None or event.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")

    # Fresh device secret; the raw value is returned once and never stored.
    raw_device = generate_token()
    participant = models.Participant(
        event_id=event.id,
        display_name=body.display_name,
        role="contributor",
        device_token_hash=hash_token(raw_device),
    )
    session.add(participant)
    await session.commit()
    await session.refresh(participant)

    return schemas.JoinResponse(
        device_token=raw_device,
        participant_id=participant.id,
        event=schemas.EventOut.model_validate(event),
    )
