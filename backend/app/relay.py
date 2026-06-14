"""Pi→Google relay (convenience mode ingest, PRODUCT_SPEC §6.3).

A single background loop: find ready convenience-mode media not yet on Google,
upload them at a gentle rate (kept well under the 10k/day pool), record the
google_media_id, then prune the Pi-local original — keeping only the thumb +
preview derivatives. On any error (quota 429, token lapse, network) the item
stays pending and is retried on the next scan; the loop is self-healing across
restarts and re-consent. Started only when google_configured (main.lifespan).
"""

import asyncio

from sqlalchemy import select

from app import google, models, storage
from app.db import SessionLocal

SCAN_INTERVAL = 10  # seconds between scans when idle
ITEM_DELAY = 1  # seconds between items (rate limit)
BATCH = 10  # items per scan


async def _relay_one(session, media: models.MediaItem, refresh_token: str) -> None:
    access = await google.get_valid_access_token(refresh_token)
    event = await session.get(models.Event, media.event_id)
    if event is None:
        return
    if not event.google_album_id:
        event.google_album_id = await google.ensure_album(
            access, f"PartySnap — {event.name}"
        )
        await session.flush()

    original_abs = storage.abspath(media.storage_key)
    upload_token = await google.upload_file(access, original_abs)
    media.google_media_id, media.google_product_url = await google.batch_create(
        access, event.google_album_id, upload_token, str(media.id)
    )

    # Pruned: original now lives on Google; Pi keeps thumb + preview only.
    storage.delete(media.storage_key)
    media.storage_key = None
    await session.commit()


async def _scan_once() -> None:
    async with SessionLocal() as session:
        account = (
            await session.execute(
                select(models.GoogleAccount)
                .where(models.GoogleAccount.status == "active")
                .limit(1)
            )
        ).scalar_one_or_none()
        if account is None:
            return
        refresh_token = google.decrypt(account.encrypted_refresh_token)

        pending = (
            await session.execute(
                select(models.MediaItem)
                .join(models.Event, models.MediaItem.event_id == models.Event.id)
                .where(
                    models.Event.storage_backend == "google_photos",
                    models.MediaItem.status == "ready",
                    models.MediaItem.google_media_id.is_(None),
                    models.MediaItem.storage_key.is_not(None),
                )
                .limit(BATCH)
            )
        ).scalars().all()

        for media in pending:
            try:
                await _relay_one(session, media, refresh_token)
            except Exception:
                # Leave pending; back off to the next scan (covers 429/token lapse).
                await session.rollback()
                break
            await asyncio.sleep(ITEM_DELAY)


async def relay_worker() -> None:
    while True:
        try:
            await _scan_once()
        except Exception:
            pass
        await asyncio.sleep(SCAN_INTERVAL)
