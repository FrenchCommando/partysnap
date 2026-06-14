"""Deletion purge (DESIGN §7, §6.2).

Two phases:
  - **immediate** — when an event (or a single item) is deleted, its on-Pi blobs
    are purged at once: that's the real privacy guarantee.
  - **deferred** — `purge_worker` hard-removes the *rows* of events soft-deleted
    past the backup-rotation window, so no DB backup outlives the promise.

Convenience-mode originals already live on Google (storage_key is None) and are
left in the admin's library (DESIGN §7); only the Pi-cached derivatives + the
album reference are dropped.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app import models, storage
from app.db import SessionLocal

PURGE_WINDOW_DAYS = 7  # backup-rotation window (PRODUCT_SPEC §6.2)
SCAN_INTERVAL = 3600  # hourly


async def purge_media_blobs(session, media: models.MediaItem) -> None:
    """Delete a media item's on-Pi blobs + derivative rows (original + thumb/preview)."""
    derivs = (
        await session.execute(
            select(models.Derivative).where(
                models.Derivative.media_item_id == media.id
            )
        )
    ).scalars().all()
    for deriv in derivs:
        storage.delete(deriv.storage_key)
        await session.delete(deriv)
    storage.delete(media.storage_key)
    media.storage_key = None

    # Relayed to Google? The API can't delete it (DESIGN §7) — log it so the admin
    # can remove it by hand before sharing the album.
    if media.google_media_id:
        session.add(
            models.GoogleDeletion(
                event_id=media.event_id,
                google_media_id=media.google_media_id,
                google_product_url=media.google_product_url,
                kind=media.kind,
                captured_at=media.captured_at,
            )
        )


async def hard_purge_event(session, event_id) -> None:
    """Remove an event and every row that depends on it, in FK-safe order."""
    media_ids = select(models.MediaItem.id).where(
        models.MediaItem.event_id == event_id
    )
    await session.execute(
        delete(models.Derivative).where(
            models.Derivative.media_item_id.in_(media_ids)
        )
    )
    event = await session.get(models.Event, event_id)
    if event is not None:
        event.cover_media_id = None  # release the cover FK before deleting media
        await session.flush()
    await session.execute(
        delete(models.MediaItem).where(models.MediaItem.event_id == event_id)
    )
    await session.execute(
        delete(models.Upload).where(models.Upload.event_id == event_id)
    )
    await session.execute(
        delete(models.ShareToken).where(models.ShareToken.event_id == event_id)
    )
    await session.execute(
        delete(models.Participant).where(models.Participant.event_id == event_id)
    )
    await session.execute(delete(models.Event).where(models.Event.id == event_id))


async def purge_worker() -> None:
    """Hourly: hard-purge events soft-deleted past the backup window."""
    while True:
        try:
            async with SessionLocal() as session:
                cutoff = datetime.now(timezone.utc) - timedelta(days=PURGE_WINDOW_DAYS)
                stale = (
                    await session.execute(
                        select(models.Event.id).where(
                            models.Event.status == "deleted",
                            models.Event.deleted_at < cutoff,
                        )
                    )
                ).scalars().all()
                for event_id in stale:
                    await hard_purge_event(session, event_id)
                if stale:
                    await session.commit()
        except Exception:
            pass
        await asyncio.sleep(SCAN_INTERVAL)
