"""Local blob storage (pi_local) — paths under the media volume (PRODUCT_SPEC §6).

A thin path/delete layer for now; the google_photos backend (slice 7b) will sit
behind the same call sites (put / url / delete).
"""

import os

from app.config import settings


def abspath(storage_key: str) -> str:
    return os.path.join(settings.media_root, storage_key)


def original_key(event_id, upload_id) -> str:
    return f"originals/{event_id}/{upload_id}"


def derivative_key(media_id, kind: str) -> str:
    return f"derivatives/{media_id}/{kind}.jpg"


def delete(storage_key: str | None) -> None:
    if not storage_key:
        return
    try:
        os.remove(abspath(storage_key))
    except FileNotFoundError:
        pass
