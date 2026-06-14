"""Google Photos client for convenience mode (PRODUCT_SPEC §6.1, §6.4).

Only used when the instance is `google_configured` and the admin has connected
an account. Covers: OAuth consent + token exchange/refresh, refresh-token
encryption at rest, album ensure, raw upload + batchCreate, and baseUrl fetch
for read-back. All network I/O is httpx; uploads stream from disk (no full-file
buffering) so large videos don't blow the Pi's RAM.
"""

import base64
import hashlib
import time
from collections.abc import AsyncIterator
from urllib.parse import urlencode

import anyio
import httpx
from cryptography.fernet import Fernet

from app.config import settings

SCOPES = " ".join(
    [
        "https://www.googleapis.com/auth/photoslibrary.appendonly",
        "https://www.googleapis.com/auth/photoslibrary.readonly.appcreateddata",
        "https://www.googleapis.com/auth/userinfo.email",
    ]
)
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://photoslibrary.googleapis.com/v1/uploads"
BATCH_CREATE_URL = "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate"
ALBUMS_URL = "https://photoslibrary.googleapis.com/v1/albums"
MEDIA_ITEM_URL = "https://photoslibrary.googleapis.com/v1/mediaItems/{id}"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


# --- refresh-token encryption at rest ----------------------------------------

def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.app_secret.encode()).digest())
    return Fernet(key)


def encrypt(token: str) -> bytes:
    return _fernet().encrypt(token.encode())


def decrypt(blob: bytes) -> str:
    return _fernet().decrypt(blob).decode()


# --- OAuth -------------------------------------------------------------------

def consent_url(state: str) -> str:
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",  # force a refresh token every time
        "state": state,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "code": code,
                "redirect_uri": settings.google_oauth_redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _refresh(refresh_token: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            TOKEN_URL,
            data={
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()


# In-memory access-token cache (single account per instance).
_cache: dict = {"token": None, "exp": 0.0}


async def get_valid_access_token(refresh_token: str) -> str:
    now = time.time()
    if _cache["token"] and _cache["exp"] - 60 > now:
        return _cache["token"]
    data = await _refresh(refresh_token)
    _cache["token"] = data["access_token"]
    _cache["exp"] = now + float(data.get("expires_in", 3600))
    return _cache["token"]


async def fetch_email(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
        )
        resp.raise_for_status()
        return resp.json().get("email", "")


# --- library ops -------------------------------------------------------------

async def ensure_album(access_token: str, title: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            ALBUMS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"album": {"title": title}},
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def _file_chunks(path: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
    fh = await anyio.to_thread.run_sync(open, path, "rb")
    try:
        while True:
            data = await anyio.to_thread.run_sync(fh.read, chunk_size)
            if not data:
                break
            yield data
    finally:
        await anyio.to_thread.run_sync(fh.close)


async def upload_file(access_token: str, path: str) -> str:
    """Raw upload, streamed from disk → returns an upload token."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/octet-stream",
        "X-Goog-Upload-Protocol": "raw",
    }
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(UPLOAD_URL, headers=headers, content=_file_chunks(path))
        resp.raise_for_status()
        return resp.text


async def batch_create(
    access_token: str, album_id: str, upload_token: str, filename: str
) -> tuple[str, str | None]:
    """Returns (media_item_id, product_url). product_url is the Google Photos link."""
    body = {
        "albumId": album_id,
        "newMediaItems": [
            {"simpleMediaItem": {"fileName": filename, "uploadToken": upload_token}}
        ],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            BATCH_CREATE_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            json=body,
        )
        resp.raise_for_status()
        item = resp.json()["newMediaItemResults"][0]["mediaItem"]
        return item["id"], item.get("productUrl")


async def get_base_url(access_token: str, media_item_id: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            MEDIA_ITEM_URL.format(id=media_item_id),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()["baseUrl"]
