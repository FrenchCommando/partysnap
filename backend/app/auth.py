"""Bearer resolution + guards for the four credential kinds (API_CONTRACT §2).

A request carries one `Authorization: Bearer <token>`. We resolve it to a
principal:
  - signed session token (contains '.')  -> Admin or Host
  - opaque token matching a device hash  -> Device (a participant)
  - opaque token matching a share token  -> Capability (contribute/view/host link)

Guards (`require_admin`, `require_host`) compose these for endpoints. Admin ⊇
host: an admin session satisfies a host requirement on any event.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models
from app.db import get_session
from app.security import hash_token, verify_session

ADMIN_SESSION_TTL = 12 * 3600
HOST_SESSION_TTL = 12 * 3600


class Scope(str, Enum):
    contribute = "contribute"
    view = "view"
    host = "host"


@dataclass
class AdminPrincipal:
    admin_id: str


@dataclass
class HostPrincipal:
    event_id: str


@dataclass
class DevicePrincipal:
    participant_id: str
    event_id: str


@dataclass
class CapabilityPrincipal:
    event_id: str
    scope: Scope


Principal = AdminPrincipal | HostPrincipal | DevicePrincipal | CapabilityPrincipal


def _bearer(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    return authorization[7:].strip()


async def get_principal(
    token: str = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    # Signed session token (admin/host) — distinguished by the '.' separator.
    if "." in token:
        claims = verify_session(token)
        if claims:
            if claims.get("kind") == "admin":
                return AdminPrincipal(admin_id=claims["sub"])
            if claims.get("kind") == "host":
                return HostPrincipal(event_id=claims["event_id"])
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired session")

    h = hash_token(token)

    # Opaque token → returning contributor (device token).
    participant = (
        await session.execute(
            select(models.Participant).where(models.Participant.device_token_hash == h)
        )
    ).scalar_one_or_none()
    if participant is not None:
        return DevicePrincipal(
            participant_id=str(participant.id), event_id=str(participant.event_id)
        )

    # Opaque token → capability link (contribute / view / host).
    st = (
        await session.execute(
            select(models.ShareToken).where(models.ShareToken.token_hash == h)
        )
    ).scalar_one_or_none()
    if st is not None and st.revoked_at is None:
        if st.expires_at is None or st.expires_at > datetime.now(timezone.utc):
            return CapabilityPrincipal(event_id=str(st.event_id), scope=Scope(st.scope))

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credential")


async def require_admin(
    principal: Principal = Depends(get_principal),
) -> AdminPrincipal:
    if not isinstance(principal, AdminPrincipal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin required")
    return principal


async def require_host(
    principal: Principal = Depends(get_principal),
) -> AdminPrincipal | HostPrincipal:
    # Admin ⊇ host (API_CONTRACT §2). A host capability link is NOT enough on its
    # own — host actions require the passcode-gated session, not the raw token.
    if isinstance(principal, (AdminPrincipal, HostPrincipal)):
        return principal
    raise HTTPException(status.HTTP_403_FORBIDDEN, "host session required")


async def require_device(
    principal: Principal = Depends(get_principal),
) -> DevicePrincipal:
    if not isinstance(principal, DevicePrincipal):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "device token required")
    return principal


def event_of(principal: Principal) -> str | None:
    """The event a principal is bound to, or None for admin (any event)."""
    if isinstance(principal, AdminPrincipal):
        return None
    if isinstance(principal, (HostPrincipal, CapabilityPrincipal, DevicePrincipal)):
        return principal.event_id
    return None


def ensure_event(principal: Principal, event_id: uuid.UUID) -> None:
    """Reject a non-admin credential scoped to a different event."""
    scoped = event_of(principal)
    if scoped is not None and scoped != str(event_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "credential is for a different event"
        )
