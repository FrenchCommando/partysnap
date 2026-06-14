"""Post-upload media processing: generate derivatives, then flip to 'ready'.

Runs as a background task off the upload request (uploads.py). Uses its own DB
session and offloads the blocking Pillow/ffmpeg work to a thread.
"""

import anyio

from app import derivatives, models, storage
from app.db import SessionLocal


async def process_media(media_id) -> None:
    async with SessionLocal() as session:
        media = await session.get(models.MediaItem, media_id)
        if media is None or media.storage_key is None:
            return

        original_abs = storage.abspath(media.storage_key)
        try:
            result = await anyio.to_thread.run_sync(
                derivatives.build, media.id, media.kind, original_abs
            )
        except Exception:
            media.status = "failed"
            await session.commit()
            return

        media.width = result.width
        media.height = result.height
        media.duration_ms = result.duration_ms
        for d in result.derivatives:
            session.add(
                models.Derivative(
                    media_item_id=media.id,
                    kind=d.kind,
                    storage_key=d.storage_key,
                    width=d.width,
                    height=d.height,
                )
            )
        media.status = "ready"
        await session.commit()
