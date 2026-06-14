"""ORM models — the schema from DESIGN §3.

Enum-like columns (`role`, `scope`, `status`, `kind`, …) are plain text with a
default, matching DESIGN's intent (no DB enum types to migrate). UUID PKs are
generated app-side via `uuid4`. Every table carries created_at/updated_at via
`TimestampMixin`.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Admin(TimestampMixin, Base):
    """Instance operator login (DESIGN §1). Env-seeded (DEPLOYMENT §5)."""

    __tablename__ = "admin"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    handle: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, server_default=text("true")
    )


class GoogleAccount(TimestampMixin, Base):
    """The admin's Google account, one per instance (DESIGN §3, convenience mode)."""

    __tablename__ = "google_account"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[bytes] = mapped_column(LargeBinary)
    scopes_granted: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    last_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GoogleDeletion(TimestampMixin, Base):
    """A Google Photos item deleted in PartySnap that the admin must remove
    by hand from their library (the API can't delete; DESIGN §7). The log
    outlives the media/event rows, so `event_id` is a plain reference (no FK)."""

    __tablename__ = "google_deletion"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    google_media_id: Mapped[str] = mapped_column(Text)
    google_product_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    cleared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )  # admin marked it handled in Google Photos


class Event(TimestampMixin, Base):
    __tablename__ = "event"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text)
    # Circular FK with media_item (cover) → emit as ALTER after both tables exist.
    cover_media_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("media_item.id", use_alter=True, name="fk_event_cover_media"),
        nullable=True,
    )
    start_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    end_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    join_policy: Mapped[str] = mapped_column(Text, server_default="open")
    storage_backend: Mapped[str] = mapped_column(Text, server_default="pi_local")
    google_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("google_account.id"), nullable=True
    )
    google_album_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, server_default="active")
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Participant(TimestampMixin, Base):
    __tablename__ = "participant"
    __table_args__ = (UniqueConstraint("event_id", "device_token_hash"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("event.id"))
    display_name: Mapped[str] = mapped_column(Text)  # required (DESIGN §5)
    role: Mapped[str] = mapped_column(Text)  # 'contributor' | 'viewer'
    device_token_hash: Mapped[str] = mapped_column(Text)


class MediaItem(TimestampMixin, Base):
    __tablename__ = "media_item"
    __table_args__ = (UniqueConstraint("event_id", "checksum_sha256"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("event.id"))
    uploader_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("participant.id"))
    kind: Mapped[str] = mapped_column(Text)  # 'photo' | 'video'
    mime_type: Mapped[str] = mapped_column(Text)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checksum_sha256: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="processing")
    # backend-specific location (exactly one set, per event.storage_backend)
    storage_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    google_product_url: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # link for the admin to find/delete it in Google Photos (DESIGN §7)


class Derivative(TimestampMixin, Base):
    __tablename__ = "derivative"
    __table_args__ = (UniqueConstraint("media_item_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    media_item_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("media_item.id")
    )
    kind: Mapped[str] = mapped_column(Text)  # 'thumb' | 'preview'
    storage_key: Mapped[str] = mapped_column(Text)  # always on Pi
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    cached_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Upload(TimestampMixin, Base):
    """In-progress resumable upload (tus). Becomes a MediaItem on completion."""

    __tablename__ = "upload"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("event.id"))
    uploader_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("participant.id"))
    filename: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(Text)  # 'photo' | 'video'
    mime_type: Mapped[str] = mapped_column(Text)
    declared_length: Mapped[int] = mapped_column(BigInteger)  # tus Upload-Length
    received_bytes: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )  # tus Upload-Offset (named to avoid the SQL reserved word OFFSET)
    checksum_sha256: Mapped[str] = mapped_column(Text)
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    storage_path: Mapped[str] = mapped_column(Text)  # partial file on the media volume
    status: Mapped[str] = mapped_column(Text, server_default="in_progress")


class ShareToken(TimestampMixin, Base):
    __tablename__ = "share_token"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("event.id"))
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    scope: Mapped[str] = mapped_column(Text)  # 'contribute' | 'view' | 'host'
    passcode_hash: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # set only for scope='host'
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
