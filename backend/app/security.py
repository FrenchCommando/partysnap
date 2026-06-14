"""Crypto primitives for the identity tiers (DESIGN §1, §4).

Three families, by what they protect:
  - opaque tokens  : high-entropy bearer secrets (share links, device tokens).
                     Stored as sha256(raw); raw is shown once, never persisted.
  - human secrets  : admin password + host passcode (typed by a person, lower
                     entropy) → argon2 slow KDF.
  - signed sessions: short-lived admin/host session tokens → HMAC-SHA256 over a
                     compact payload. Not JWT (fixed algorithm, no negotiation).
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.config import settings

_ph = PasswordHasher()


# --- opaque tokens -----------------------------------------------------------

def generate_token(nbytes: int = 16) -> str:
    """A URL-safe opaque secret (128 bits by default). DESIGN §4."""
    return secrets.token_urlsafe(nbytes)


def hash_token(raw: str) -> str:
    """sha256 hex — what we store for high-entropy tokens."""
    return hashlib.sha256(raw.encode()).hexdigest()


# Unambiguous alphabet (no 0/O/1/I/L) — the passcode is typed by a person.
_PASSCODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_passcode(length: int = 8) -> str:
    """A short, human-typable secret (the host passcode). Hashed with argon2."""
    return "".join(secrets.choice(_PASSCODE_ALPHABET) for _ in range(length))


# --- human secrets (argon2) --------------------------------------------------

def hash_secret(raw: str) -> str:
    return _ph.hash(raw)


def verify_secret(raw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, raw)
    except VerifyMismatchError:
        return False


# --- signed session tokens ---------------------------------------------------

def _b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(body: str) -> str:
    mac = hmac.new(settings.app_secret.encode(), body.encode(), hashlib.sha256)
    return _b64u(mac.digest())


def mint_session(claims: dict, ttl_seconds: int, now: int | None = None) -> str:
    """Sign `claims` (+ `exp`) into a `body.sig` token. Opaque tokens never
    contain '.', so the separator distinguishes a session from a bearer secret."""
    now = int(time.time()) if now is None else now
    payload = {**claims, "exp": now + ttl_seconds}
    body = _b64u(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_sign(body)}"


def verify_session(token: str, now: int | None = None) -> dict | None:
    """Return claims if signature + expiry are valid, else None."""
    now = int(time.time()) if now is None else now
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(body)):
        return None
    try:
        claims = json.loads(_b64u_decode(body))
    except (ValueError, json.JSONDecodeError):
        return None
    if int(claims.get("exp", 0)) < now:
        return None
    return claims
