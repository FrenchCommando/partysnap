"""Resumable uploads — hand-rolled tus 1.0 (creation) — API_CONTRACT §7.

POST creates an upload (after the per-guest cap, convenience-mode video ceiling,
and dedup checks); PATCH appends chunks by offset; HEAD reports the offset for
resume. On completion the partial file becomes the stored original and a
MediaItem (status='ready'). Derivative generation + byte-serving are slice 7.

Auth is the guest device token — the upload is scoped to that device's event and
attributed to it.
"""

import base64
import os
import uuid
from datetime import datetime

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app import models, storage
from app.auth import DevicePrincipal, require_device
from app.config import settings
from app.db import get_session
from app.processing import process_media

router = APIRouter(prefix="/api", tags=["uploads"])

TUS_VERSION = "1.0.0"


# --- helpers -----------------------------------------------------------------

def _parse_metadata(header: str | None) -> dict[str, str]:
    """tus Upload-Metadata: comma-separated `key <base64-value>` pairs."""
    out: dict[str, str] = {}
    for pair in (header or "").split(","):
        parts = pair.strip().split(" ", 1)
        key = parts[0]
        if not key:
            continue
        if len(parts) == 2:
            try:
                out[key] = base64.b64decode(parts[1]).decode()
            except ValueError:
                out[key] = ""
        else:
            out[key] = ""
    return out


def _parse_captured_at(val: str) -> datetime | None:
    try:
        return datetime.fromisoformat(val) if val else None
    except ValueError:
        return None


def _tus(extra: dict | None = None) -> dict:
    headers = {"Tus-Resumable": TUS_VERSION}
    if extra:
        headers.update(extra)
    return headers


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _touch(path: str) -> None:
    open(path, "ab").close()


def _open_append(path: str):
    return open(path, "ab")


async def _finalize(session: AsyncSession, upload: models.Upload) -> models.MediaItem:
    """Move the partial to its original location and create the MediaItem."""
    final_rel = storage.original_key(upload.event_id, upload.id)
    final_abs = storage.abspath(final_rel)
    await anyio.to_thread.run_sync(_ensure_dir, os.path.dirname(final_abs))
    await anyio.to_thread.run_sync(os.replace, upload.storage_path, final_abs)
    media = models.MediaItem(
        event_id=upload.event_id,
        uploader_id=upload.uploader_id,
        kind=upload.kind,
        mime_type=upload.mime_type,
        byte_size=upload.declared_length,
        checksum_sha256=upload.checksum_sha256,
        captured_at=upload.captured_at,
        status="processing",  # derivatives run async, then -> 'ready' (processing.py)
        storage_key=final_rel,
    )
    session.add(media)
    upload.status = "completed"
    return media


async def _load_owned(
    session: AsyncSession, upload_id: uuid.UUID, principal: DevicePrincipal
) -> models.Upload:
    upload = await session.get(models.Upload, upload_id)
    if upload is None or upload.uploader_id != uuid.UUID(principal.participant_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "upload not found")
    return upload


# --- endpoints ---------------------------------------------------------------

@router.options("/uploads")
async def upload_options() -> Response:
    return Response(
        status_code=204,
        headers=_tus(
            {
                "Tus-Version": TUS_VERSION,
                "Tus-Extension": "creation",
                "Tus-Max-Size": str(settings.per_guest_cap_bytes),
            }
        ),
    )


@router.post("/uploads")
async def create_upload(
    request: Request,
    principal: DevicePrincipal = Depends(require_device),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        length = int(request.headers.get("Upload-Length", ""))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload-Length required")
    if length <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload-Length must be > 0")

    meta = _parse_metadata(request.headers.get("Upload-Metadata"))
    kind = meta.get("kind")
    checksum = meta.get("checksum_sha256")
    if kind not in ("photo", "video") or not checksum:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing kind/checksum metadata")

    event = await session.get(models.Event, uuid.UUID(principal.event_id))
    if event is None or event.status == "deleted":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "event not found")

    if (
        event.storage_backend == "google_photos"
        and kind == "video"
        and length > settings.google_video_max_bytes
    ):
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            "video exceeds the ~20 GB Google limit (convenience mode)",
        )

    # Dedup (idempotent re-upload): same checksum already in this event.
    existing = (
        await session.execute(
            select(models.MediaItem).where(
                models.MediaItem.event_id == event.id,
                models.MediaItem.checksum_sha256 == checksum,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return JSONResponse(
            {"media_item_id": str(existing.id), "duplicate": True},
            status_code=status.HTTP_200_OK,
            headers=_tus(),
        )

    # Per-guest anti-abuse cap (PRODUCT_SPEC §5).
    used = (
        await session.execute(
            select(func.coalesce(func.sum(models.MediaItem.byte_size), 0)).where(
                models.MediaItem.uploader_id == uuid.UUID(principal.participant_id)
            )
        )
    ).scalar_one()
    if used + length > settings.per_guest_cap_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "per-guest upload cap exceeded"
        )

    upload = models.Upload(
        event_id=event.id,
        uploader_id=uuid.UUID(principal.participant_id),
        filename=meta.get("filename", ""),
        kind=kind,
        mime_type=meta.get("filetype") or meta.get("mime_type") or "application/octet-stream",
        declared_length=length,
        checksum_sha256=checksum,
        captured_at=_parse_captured_at(meta.get("captured_at", "")),
        storage_path="",
        status="in_progress",
    )
    session.add(upload)
    await session.flush()  # assign upload.id
    upload.storage_path = os.path.join(
        settings.media_root, "uploads", f"{upload.id}.part"
    )
    await anyio.to_thread.run_sync(_ensure_dir, os.path.dirname(upload.storage_path))
    await anyio.to_thread.run_sync(_touch, upload.storage_path)
    await session.commit()

    return Response(
        status_code=status.HTTP_201_CREATED,
        headers=_tus({"Location": f"/api/uploads/{upload.id}", "Upload-Offset": "0"}),
    )


@router.head("/uploads/{upload_id}")
async def head_upload(
    upload_id: uuid.UUID,
    principal: DevicePrincipal = Depends(require_device),
    session: AsyncSession = Depends(get_session),
) -> Response:
    upload = await _load_owned(session, upload_id, principal)
    return Response(
        status_code=200,
        headers=_tus(
            {
                "Upload-Offset": str(upload.received_bytes),
                "Upload-Length": str(upload.declared_length),
                "Cache-Control": "no-store",
            }
        ),
    )


@router.patch("/uploads/{upload_id}")
async def patch_upload(
    upload_id: uuid.UUID,
    request: Request,
    principal: DevicePrincipal = Depends(require_device),
    session: AsyncSession = Depends(get_session),
) -> Response:
    upload = await _load_owned(session, upload_id, principal)
    if upload.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "upload already completed")
    try:
        client_offset = int(request.headers.get("Upload-Offset", ""))
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Upload-Offset required")
    if client_offset != upload.received_bytes:
        raise HTTPException(status.HTTP_409_CONFLICT, "offset mismatch")

    offset = upload.received_bytes
    overflow = False
    handle = await anyio.to_thread.run_sync(_open_append, upload.storage_path)
    try:
        async for chunk in request.stream():
            if offset + len(chunk) > upload.declared_length:
                overflow = True
                break
            await anyio.to_thread.run_sync(handle.write, chunk)
            offset += len(chunk)
    finally:
        await anyio.to_thread.run_sync(handle.close)

    upload.received_bytes = offset  # persist actual progress even on overflow
    completed_media_id = None
    if not overflow and offset == upload.declared_length:
        media = await _finalize(session, upload)
        await session.flush()
        completed_media_id = media.id
    await session.commit()

    if overflow:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "upload exceeds declared length"
        )

    headers = _tus({"Upload-Offset": str(offset)})
    if completed_media_id is not None:
        headers["PartySnap-Media-Id"] = str(completed_media_id)
    response = Response(status_code=204, headers=headers)
    if completed_media_id is not None:
        # Generate derivatives off the request; flips status processing -> ready.
        response.background = BackgroundTask(process_media, completed_media_id)
    return response
