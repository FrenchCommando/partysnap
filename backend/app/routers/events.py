"""Event management + share tokens — API_CONTRACT §4, §5.

Event creation is admin-authed (decided, API_CONTRACT §4) and mints the first
host credential (link + one-time passcode). Management (patch/delete/rotate/share
tokens) accepts an admin or a host session scoped to that event.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models, purge, schemas
from app.auth import (
    AdminPrincipal,
    HostPrincipal,
    Principal,
    ensure_event,
    get_principal,
    require_admin,
    require_host,
)
from app.config import settings
from app.db import get_session
from app.security import generate_passcode, generate_token, hash_secret, hash_token

router = APIRouter(prefix="/api", tags=["events"])


def _link(token: str) -> str:
    return f"https://{settings.partysnap_domain}/e/{token}"


async def _active_event(session: AsyncSession, event_id: uuid.UUID) -> models.Event:
    event = await session.get(models.Event, event_id)
    if event is None or event.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")
    return event


async def _new_host_credential(session: AsyncSession, event_id: uuid.UUID) -> schemas.HostCredential:
    raw_token = generate_token()
    raw_passcode = generate_passcode()
    session.add(
        models.ShareToken(
            event_id=event_id,
            token_hash=hash_token(raw_token),
            scope="host",
            passcode_hash=hash_secret(raw_passcode),
            label="host",
        )
    )
    return schemas.HostCredential(host_link=_link(raw_token), host_passcode=raw_passcode)


@router.post("/events", response_model=schemas.EventCreated, status_code=201)
async def create_event(
    body: schemas.EventCreate,
    _: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> schemas.EventCreated:
    if body.storage_backend not in ("pi_local", "google_photos"):
        raise HTTPException(422, "invalid storage_backend")
    google_account_id = None
    if body.storage_backend == "google_photos":
        ga = (
            await session.execute(
                select(models.GoogleAccount).where(
                    models.GoogleAccount.status == "active"
                ).limit(1)
            )
        ).scalar_one_or_none()
        if ga is None:
            raise HTTPException(400, "convenience mode requires a connected Google account")
        google_account_id = ga.id

    event = models.Event(
        name=body.name,
        cover_media_id=body.cover_media_id,
        start_at=body.start_at,
        end_at=body.end_at,
        join_policy=body.join_policy,
        storage_backend=body.storage_backend,
        google_account_id=google_account_id,
    )
    session.add(event)
    await session.flush()  # assign event.id before minting the host token

    credential = await _new_host_credential(session, event.id)
    await session.commit()
    await session.refresh(event)  # pick up server-side defaults (status, …)
    return schemas.EventCreated(
        event=schemas.EventOut.model_validate(event),
        host_link=credential.host_link,
        host_passcode=credential.host_passcode,
    )


@router.get("/events/{event_id}", response_model=schemas.EventOut)
async def get_event(
    event_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> schemas.EventOut:
    ensure_event(principal, event_id)  # admin, host session, or any capability for this event
    event = await _active_event(session, event_id)
    return schemas.EventOut.model_validate(event)


@router.patch("/events/{event_id}", response_model=schemas.EventOut)
async def patch_event(
    event_id: uuid.UUID,
    body: schemas.EventPatch,
    principal: AdminPrincipal | HostPrincipal = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> schemas.EventOut:
    ensure_event(principal, event_id)
    event = await _active_event(session, event_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    await session.commit()
    await session.refresh(event)
    return schemas.EventOut.model_validate(event)


@router.delete("/events/{event_id}", status_code=202)
async def delete_event(
    event_id: uuid.UUID,
    principal: AdminPrincipal | HostPrincipal = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # Blobs purged immediately (DESIGN §7); rows soft-deleted now and hard-purged
    # after the backup window by purge.purge_worker (§6.2).
    ensure_event(principal, event_id)
    event = await _active_event(session, event_id)
    media_list = (
        await session.execute(
            select(models.MediaItem).where(models.MediaItem.event_id == event_id)
        )
    ).scalars().all()
    for media in media_list:
        await purge.purge_media_blobs(session, media)
        media.status = "deleted"
    event.google_album_id = None  # forget the convenience album (bytes stay in Google)
    event.status = "deleted"
    event.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    return Response(status_code=202)


@router.post(
    "/events/{event_id}/host-credential/rotate",
    response_model=schemas.HostCredential,
)
async def rotate_host_credential(
    event_id: uuid.UUID,
    principal: AdminPrincipal | HostPrincipal = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> schemas.HostCredential:
    ensure_event(principal, event_id)
    await _active_event(session, event_id)
    existing = (
        await session.execute(
            select(models.ShareToken).where(
                models.ShareToken.event_id == event_id,
                models.ShareToken.scope == "host",
                models.ShareToken.revoked_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.now(timezone.utc)
    for tok in existing:
        tok.revoked_at = now
    credential = await _new_host_credential(session, event_id)
    await session.commit()
    return credential


@router.get(
    "/events/{event_id}/share-tokens", response_model=list[schemas.ShareTokenOut]
)
async def list_share_tokens(
    event_id: uuid.UUID,
    principal: AdminPrincipal | HostPrincipal = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.ShareTokenOut]:
    ensure_event(principal, event_id)
    await _active_event(session, event_id)
    rows = (
        await session.execute(
            select(models.ShareToken).where(models.ShareToken.event_id == event_id)
        )
    ).scalars().all()
    return [schemas.ShareTokenOut.model_validate(r) for r in rows]


@router.post(
    "/events/{event_id}/share-tokens",
    response_model=schemas.ShareTokenCreated,
    status_code=201,
)
async def create_share_token(
    event_id: uuid.UUID,
    body: schemas.ShareTokenCreate,
    principal: AdminPrincipal | HostPrincipal = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> schemas.ShareTokenCreated:
    if body.scope not in ("contribute", "view"):
        raise HTTPException(422, "scope must be 'contribute' or 'view'")
    ensure_event(principal, event_id)
    await _active_event(session, event_id)
    raw = generate_token()
    st = models.ShareToken(
        event_id=event_id,
        token_hash=hash_token(raw),
        scope=body.scope,
        label=body.label,
        expires_at=body.expires_at,
    )
    session.add(st)
    await session.commit()
    await session.refresh(st)
    return schemas.ShareTokenCreated(id=st.id, scope=st.scope, link=_link(raw))


@router.post("/share-tokens/{token_id}/revoke", status_code=204)
async def revoke_share_token(
    token_id: uuid.UUID,
    principal: AdminPrincipal | HostPrincipal = Depends(require_host),
    session: AsyncSession = Depends(get_session),
) -> Response:
    st = await session.get(models.ShareToken, token_id)
    if st is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "share token not found")
    ensure_event(principal, st.event_id)
    st.revoked_at = datetime.now(timezone.utc)
    await session.commit()
    return Response(status_code=204)
