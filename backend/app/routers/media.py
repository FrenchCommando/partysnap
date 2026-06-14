"""Gallery + item detail + self-delete — API_CONTRACT §8.

Byte-serving (thumb/preview/original) lands with the storage slice; the `urls`
here are the stable paths those endpoints will answer. Delete is soft for now
(blob purge + row removal come with storage, DESIGN §7).
"""

import base64
import os
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import google, models, purge, schemas, storage
from app.auth import (
    AdminPrincipal,
    DevicePrincipal,
    HostPrincipal,
    Principal,
    ensure_event,
    get_principal,
)
from app.db import get_session

router = APIRouter(prefix="/api", tags=["media"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 100


def _urls(media_id: uuid.UUID) -> schemas.MediaUrls:
    base = f"/api/media/{media_id}"
    return schemas.MediaUrls(
        thumb=f"{base}/thumb", preview=f"{base}/preview", original=f"{base}/original"
    )


def _to_out(media: models.MediaItem, display_name: str) -> schemas.MediaItemOut:
    return schemas.MediaItemOut(
        id=media.id,
        kind=media.kind,
        mime_type=media.mime_type,
        byte_size=media.byte_size,
        width=media.width,
        height=media.height,
        duration_ms=media.duration_ms,
        captured_at=media.captured_at,
        status=media.status,
        uploader=schemas.Uploader(
            participant_id=media.uploader_id, display_name=display_name
        ),
        urls=_urls(media.id),
    )


def _sort_value(media: models.MediaItem) -> datetime:
    # Capture time when known (EXIF), upload time as fallback.
    return media.captured_at or media.created_at


def _encode_cursor(media: models.MediaItem) -> str:
    raw = f"{_sort_value(media).isoformat()}|{media.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        created_at, item_id = raw.split("|", 1)
        return datetime.fromisoformat(created_at), uuid.UUID(item_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


@router.get("/events/{event_id}/media", response_model=schemas.GalleryPage)
async def list_media(
    event_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = DEFAULT_LIMIT,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> schemas.GalleryPage:
    ensure_event(principal, event_id)
    limit = max(1, min(limit, MAX_LIMIT))

    # Timeline order: capture time when known, upload time as fallback (DESIGN).
    # Keyset paging on (sort_key desc, id desc); gallery shows ready items only.
    sort_key = func.coalesce(
        models.MediaItem.captured_at, models.MediaItem.created_at
    )
    stmt = (
        select(models.MediaItem, models.Participant.display_name)
        .join(models.Participant, models.MediaItem.uploader_id == models.Participant.id)
        .where(
            models.MediaItem.event_id == event_id,
            models.MediaItem.status == "ready",
        )
    )
    if cursor:
        c_val, c_id = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(
                sort_key < c_val,
                and_(sort_key == c_val, models.MediaItem.id < c_id),
            )
        )
    stmt = stmt.order_by(sort_key.desc(), models.MediaItem.id.desc()).limit(limit + 1)

    rows = (await session.execute(stmt)).all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [_to_out(media, name) for media, name in rows]
    next_cursor = _encode_cursor(rows[-1][0]) if has_more and rows else None
    return schemas.GalleryPage(items=items, next_cursor=next_cursor)


@router.get("/media/{media_id}", response_model=schemas.MediaItemOut)
async def get_media(
    media_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> schemas.MediaItemOut:
    row = (
        await session.execute(
            select(models.MediaItem, models.Participant.display_name)
            .join(
                models.Participant,
                models.MediaItem.uploader_id == models.Participant.id,
            )
            .where(models.MediaItem.id == media_id)
        )
    ).first()
    if row is None or row[0].status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    media, name = row
    ensure_event(principal, media.event_id)
    return _to_out(media, name)


@router.delete("/media/{media_id}", status_code=204)
async def delete_media(
    media_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    media = await session.get(models.MediaItem, media_id)
    if media is None or media.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")

    # Uploader (own device token) or host/admin — a bare capability link cannot delete.
    if isinstance(principal, AdminPrincipal):
        pass
    elif isinstance(principal, HostPrincipal):
        if principal.event_id != str(media.event_id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "host session is for a different event"
            )
    elif isinstance(principal, DevicePrincipal):
        if principal.participant_id != str(media.uploader_id):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, "you can only delete your own uploads"
            )
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "not permitted")

    # Immediate hard self-delete (DESIGN §7): unset cover, purge blobs, remove row.
    event = await session.get(models.Event, media.event_id)
    if event is not None and event.cover_media_id == media.id:
        event.cover_media_id = None
    await purge.purge_media_blobs(session, media)
    await session.delete(media)
    await session.commit()
    return Response(status_code=204)


# --- byte serving (pi_local; app-streamed per DEPLOYMENT §6) -----------------

async def _serve_derivative(
    session: AsyncSession, principal: Principal, media_id: uuid.UUID, kind: str
) -> FileResponse:
    media = await session.get(models.MediaItem, media_id)
    if media is None or media.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    ensure_event(principal, media.event_id)
    deriv = (
        await session.execute(
            select(models.Derivative).where(
                models.Derivative.media_item_id == media_id,
                models.Derivative.kind == kind,
            )
        )
    ).scalar_one_or_none()
    if deriv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} not ready")
    path = storage.abspath(deriv.storage_key)
    if not os.path.exists(path):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} not found")
    return FileResponse(path, media_type="image/jpeg")


@router.get("/media/{media_id}/thumb")
async def media_thumb(
    media_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    return await _serve_derivative(session, principal, media_id, "thumb")


@router.get("/media/{media_id}/preview")
async def media_preview(
    media_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    return await _serve_derivative(session, principal, media_id, "preview")


@router.get("/media/{media_id}/original")
async def media_original(
    media_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    media = await session.get(models.MediaItem, media_id)
    if media is None or media.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "media not found")
    ensure_event(principal, media.event_id)

    # On the Pi: privacy mode, or convenience before the relay prunes it.
    if media.storage_key:
        path = storage.abspath(media.storage_key)
        if os.path.exists(path):
            return FileResponse(path, media_type=media.mime_type)

    # Convenience mode, relayed: redirect to Google's CDN baseUrl; degrade to the
    # Pi-cached preview if Google is unavailable/over quota (PRODUCT_SPEC §6.3).
    if media.google_media_id:
        account = (
            await session.execute(
                select(models.GoogleAccount)
                .where(models.GoogleAccount.status == "active")
                .limit(1)
            )
        ).scalar_one_or_none()
        if account is not None:
            try:
                access = await google.get_valid_access_token(
                    google.decrypt(account.encrypted_refresh_token)
                )
                base_url = await google.get_base_url(access, media.google_media_id)
                return RedirectResponse(f"{base_url}=d")
            except Exception:
                pass
        return await _serve_derivative(session, principal, media_id, "preview")

    raise HTTPException(status.HTTP_404_NOT_FOUND, "original not available")
