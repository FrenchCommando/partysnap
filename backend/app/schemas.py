"""Request/response models for the API (API_CONTRACT)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# --- admin ---

class AdminLogin(BaseModel):
    handle: str
    password: str


class AdminSession(BaseModel):
    admin_token: str
    expires_at: datetime
    must_change_password: bool


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class InstanceOut(BaseModel):
    storage_backends_available: list[str]
    google: dict


class GoogleDeletionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    google_media_id: str
    google_product_url: str | None
    kind: str
    captured_at: datetime | None
    deleted_at: datetime


# --- host session exchange ---

class HostLogin(BaseModel):
    token: str
    passcode: str


class HostSession(BaseModel):
    host_token: str
    expires_at: datetime


# --- events ---

class EventCreate(BaseModel):
    name: str
    cover_media_id: uuid.UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    join_policy: str = "open"
    storage_backend: str = "pi_local"


class EventPatch(BaseModel):
    name: str | None = None
    cover_media_id: uuid.UUID | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    join_policy: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    cover_media_id: uuid.UUID | None
    start_at: datetime | None
    end_at: datetime | None
    join_policy: str
    storage_backend: str
    status: str


class HostCredential(BaseModel):
    host_link: str
    host_passcode: str


class EventCreated(BaseModel):
    event: EventOut
    host_link: str
    host_passcode: str


# --- share tokens ---

class ShareTokenCreate(BaseModel):
    scope: str  # 'contribute' | 'view'
    label: str | None = None
    expires_at: datetime | None = None


class ShareTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    scope: str
    label: str | None
    expires_at: datetime | None
    revoked_at: datetime | None


class ShareTokenCreated(BaseModel):
    id: uuid.UUID
    scope: str
    link: str


# --- join (guest) ---

class JoinRequest(BaseModel):
    share_token: str
    display_name: str = Field(min_length=1)  # required (DESIGN §5)


class JoinResponse(BaseModel):
    device_token: str
    participant_id: uuid.UUID
    event: EventOut


# --- media / gallery ---

class Uploader(BaseModel):
    participant_id: uuid.UUID
    display_name: str


class MediaUrls(BaseModel):
    thumb: str
    preview: str
    original: str


class MediaItemOut(BaseModel):
    id: uuid.UUID
    kind: str
    mime_type: str
    byte_size: int
    width: int | None
    height: int | None
    duration_ms: int | None
    captured_at: datetime | None
    status: str
    uploader: Uploader
    urls: MediaUrls


class GalleryPage(BaseModel):
    items: list[MediaItemOut]
    next_cursor: str | None
