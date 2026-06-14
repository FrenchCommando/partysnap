"""Admin (instance operator) endpoints — API_CONTRACT §3."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import google, models, schemas
from app.auth import ADMIN_SESSION_TTL, AdminPrincipal, require_admin
from app.config import settings
from app.db import get_session
from app.security import hash_secret, mint_session, verify_secret, verify_session

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/sessions", response_model=schemas.AdminSession)
async def login(
    body: schemas.AdminLogin, session: AsyncSession = Depends(get_session)
) -> schemas.AdminSession:
    admin = (
        await session.execute(
            select(models.Admin).where(models.Admin.handle == body.handle)
        )
    ).scalar_one_or_none()
    if admin is None or not verify_secret(body.password, admin.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token = mint_session({"kind": "admin", "sub": str(admin.id)}, ADMIN_SESSION_TTL)
    return schemas.AdminSession(
        admin_token=token,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=ADMIN_SESSION_TTL),
        must_change_password=admin.must_change_password,
    )


@router.delete("/sessions/current", status_code=204)
async def logout(_: AdminPrincipal = Depends(require_admin)) -> Response:
    # Stateless signed sessions — logout is a client-side discard (no server state).
    return Response(status_code=204)


@router.post("/password", status_code=204)
async def change_password(
    body: schemas.PasswordChange,
    principal: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    admin = await session.get(models.Admin, uuid.UUID(principal.admin_id))
    if admin is None or not verify_secret(body.current_password, admin.password_hash):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "current password incorrect")
    admin.password_hash = hash_secret(body.new_password)
    admin.must_change_password = False
    await session.commit()
    return Response(status_code=204)


@router.get("/instance", response_model=schemas.InstanceOut)
async def instance(
    _: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> schemas.InstanceOut:
    ga = (
        await session.execute(select(models.GoogleAccount).limit(1))
    ).scalar_one_or_none()
    connected = ga is not None and ga.status == "active"
    google_status: dict = {
        "configured": settings.google_configured,
        "connected": connected,
    }
    if connected:
        # Testing-mode refresh token lapses 7 days after consent (PRODUCT_SPEC §6.4).
        expires = (
            ga.last_refreshed_at + timedelta(days=7) if ga.last_refreshed_at else None
        )
        google_status.update(
            {
                "email": ga.email,
                "status": ga.status,
                "token_expires_at": expires.isoformat() if expires else None,
            }
        )
    backends = ["pi_local"] + (["google_photos"] if connected else [])
    return schemas.InstanceOut(
        storage_backends_available=backends, google=google_status
    )


@router.post("/google/oauth/start")
async def google_oauth_start(_: AdminPrincipal = Depends(require_admin)) -> dict:
    if not settings.google_configured:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Google is not configured on this instance"
        )
    state = mint_session({"kind": "oauth_state"}, 600)
    return {"consent_url": google.consent_url(state)}


@router.get("/google/oauth/callback")
async def google_oauth_callback(
    code: str = "",
    state: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    # Public endpoint (Google redirects the browser here); authorized by the
    # signed state from /start, not an admin Bearer.
    if not settings.google_configured:
        raise HTTPException(status.HTTP_409_CONFLICT, "Google is not configured")
    claims = verify_session(state)
    if not claims or claims.get("kind") != "oauth_state":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid or expired state")
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "missing code")
    try:
        tokens = await google.exchange_code(code)
    except Exception:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Google token exchange failed")
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no refresh token returned; re-consent with a fresh prompt",
        )
    email = ""
    try:
        email = await google.fetch_email(tokens["access_token"])
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    account = (
        await session.execute(select(models.GoogleAccount).limit(1))
    ).scalar_one_or_none()
    if account is None:
        session.add(
            models.GoogleAccount(
                email=email,
                encrypted_refresh_token=google.encrypt(refresh_token),
                scopes_granted=tokens.get("scope", ""),
                status="active",
                last_refreshed_at=now,
            )
        )
    else:
        account.email = email or account.email
        account.encrypted_refresh_token = google.encrypt(refresh_token)
        account.scopes_granted = tokens.get("scope", account.scopes_granted)
        account.status = "active"
        account.last_refreshed_at = now
    await session.commit()
    return HTMLResponse("<p>Google connected. You can close this window.</p>")


@router.get("/google/deletions", response_model=list[schemas.GoogleDeletionOut])
async def google_deletions(
    _: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[schemas.GoogleDeletionOut]:
    # Items deleted in PartySnap but still in the admin's Google Photos — to remove
    # by hand before sharing the album (DESIGN §7).
    rows = (
        await session.execute(
            select(models.GoogleDeletion)
            .where(models.GoogleDeletion.cleared_at.is_(None))
            .order_by(models.GoogleDeletion.deleted_at)
        )
    ).scalars().all()
    return [schemas.GoogleDeletionOut.model_validate(r) for r in rows]


@router.post("/google/deletions/clear", status_code=204)
async def clear_google_deletions(
    _: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # "I've removed these from Google Photos" — mark all pending entries handled.
    await session.execute(
        update(models.GoogleDeletion)
        .where(models.GoogleDeletion.cleared_at.is_(None))
        .values(cleared_at=func.now())
    )
    await session.commit()
    return Response(status_code=204)


@router.delete("/google", status_code=204)
async def google_disconnect(
    _: AdminPrincipal = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    account = (
        await session.execute(select(models.GoogleAccount).limit(1))
    ).scalar_one_or_none()
    if account is not None:
        account.status = "revoked"  # convenience events degrade; relay stops
        await session.commit()
    return Response(status_code=204)
