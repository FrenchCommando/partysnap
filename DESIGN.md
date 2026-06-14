# PartySnap — Data Model & Auth Design

> Status: Draft · Companion to [PRODUCT_SPEC.md](./PRODUCT_SPEC.md)
> Scope: Postgres schema + the capability/identity model. Backend is FastAPI + Postgres on the Pi (PRODUCT_SPEC §7).

## 1. Identity & authority systems (keep them separate)

PartySnap has several distinct notions of "who," plus one external authorization. Conflating them is the classic mistake, so they're named up front:

1. **Admin (instance operator)** — the person who runs the Pi/instance. A durable **operator login** (handle + password), set once at instance setup. Manages the deployment, every event, storage/OAuth config, and owns the instance's Google account. One per instance; in a personal deployment it's the same person who hosts events, but the role is separate.
2. **Capability tokens** — authorize *actions on an event*. The share link/QR.
   - `contribute` / `view` are **pure bearer** — possession = access, no PII.
   - `host` is a **gated link**: the host link *plus* a passcode (one credential split in two — *not* two-factor; both are secrets the holder presents together). Shared by co-hosts; holders are indistinguishable; revocation rotates both.
3. **Device tokens** — identify a *returning contributor* on a device, so "my uploads," attribution, and self-delete work. Opaque bearer secret, no PII (the human-facing name lives in `participant.display_name`, not the token).
4. **Google OAuth (convenience mode only)** — the **admin's** authorization for PartySnap to write to the **admin's** Google Photos. Not a PartySnap login. See PRODUCT_SPEC §6.4.

(1)–(3) are PartySnap's own; (4) is external and touches only the admin.

## 2. Entities (overview)

```
Event ──< ShareToken
  │         (capability links: contribute / view / host)
  ├──< Participant ──< MediaItem ──< Derivative
  │      (contributor/viewer,       (photo/video)   (thumb/preview, always on Pi)
  │       device-scoped)
  └──> GoogleAccount?   (nullable; convenience mode only)
```

- An **Event** has many **Participants**, many **MediaItems**, many **ShareTokens**.
- A **Participant** belongs to one event and uploads many **MediaItems**.
- A **MediaItem** has one or more **Derivatives** (thumbnail, preview), which always live on the Pi (PRODUCT_SPEC §6.3).
- An **Event** in convenience mode references the instance's one **GoogleAccount** (the **admin's**).

## 3. Schema (Postgres, sketch)

UUID PKs throughout. `created_at`/`updated_at` omitted from listings for brevity but present on every table. FKs carry **no DB-level `ON DELETE`**; deletion cascades are handled in application code (`purge.py`, §7).

### admin
```sql
admin (
  id                   uuid pk,
  handle               text not null unique,   -- operator login name (env-seeded, DEPLOYMENT §5)
  password_hash        text not null,          -- argon2
  must_change_password boolean not null default true
)
```
The instance operator (§1) — one env-seeded row; the durable management credential, separate from the per-event capability tokens.

### event
```sql
event (
  id              uuid pk,
  name            text not null,
  cover_media_id  uuid null fk -> media_item(id),
  start_at        timestamptz null,        -- optional event window
  end_at          timestamptz null,
  join_policy     text not null default 'open',     -- 'open' | 'approval'(later)
  storage_backend text not null default 'pi_local', -- 'pi_local' | 'google_photos'
  google_account_id uuid null fk -> google_account(id), -- the admin's account; convenience mode only
  google_album_id   text null,             -- the per-event album in the admin's library
  status          text not null default 'active',   -- 'active' | 'deleted'
  deleted_at      timestamptz null
)
```
`storage_backend` is the discriminator from PRODUCT_SPEC §6 — set at creation, **immutable** (changing it would mean migrating bytes); switching modes means creating a new event. Convenience is selectable only when the admin has connected the instance Google account.

### participant
```sql
participant (
  id                uuid pk,
  event_id          uuid not null fk -> event(id),
  display_name      text not null,         -- collected at join; attribution depends on it
  role              text not null,         -- 'contributor' | 'viewer'
  device_token_hash text not null,         -- sha256 of the device bearer secret
  unique (event_id, device_token_hash)
)
```
Per-event identity — a device that joins two events has two participant rows. No global account required. **Host management authority comes from the gated `host` capability (§4), not from a participant row** — a host who also uploads simply joins as a `contributor` like anyone else.

The **per-guest anti-abuse cap** (PRODUCT_SPEC §5, ~100 GB, deliberately high — a guardrail, not a quota) is enforced as `sum(media_item.byte_size) where uploader_id = participant.id`, checked before accepting each upload — no per-file or per-event cap.

### media_item
```sql
media_item (
  id                uuid pk,
  event_id          uuid not null fk -> event(id),
  uploader_id       uuid not null fk -> participant(id),
  kind              text not null,         -- 'photo' | 'video'
  mime_type         text not null,
  byte_size         bigint not null,
  width             int null,
  height            int null,
  duration_ms       int null,              -- video only
  captured_at       timestamptz null,      -- from EXIF; null if absent
  checksum_sha256   text not null,         -- dedup within an event
  status            text not null default 'processing', -- 'processing'|'ready'|'failed'|'deleted' (deleted = soft, via event deletion; self-delete hard-removes the row)
  -- backend-specific location (exactly one set, per event.storage_backend):
  storage_key       text null,             -- pi_local: path/object key on Pi
  google_media_id   text null,             -- google_photos: Google's mediaItem id
  google_product_url text null,            -- google_photos: link for manual deletion (§7)
  unique (event_id, checksum_sha256)       -- idempotent re-upload / dedup
)
```
EXIF is preserved in the stored original (PRODUCT_SPEC §6); `captured_at`/dimensions are *denormalized* from it for sorting and display without re-reading the file.

### upload
```sql
upload (
  id              uuid pk,
  event_id        uuid not null fk -> event(id),
  uploader_id     uuid not null fk -> participant(id),
  filename        text not null,
  kind            text not null,              -- 'photo' | 'video'
  mime_type       text not null,
  declared_length bigint not null,            -- tus Upload-Length
  received_bytes  bigint not null default 0,  -- tus Upload-Offset
  checksum_sha256 text not null,
  captured_at     timestamptz null,
  storage_path    text not null,              -- partial file on the media volume
  status          text not null default 'in_progress'  -- 'in_progress' | 'completed'
)
```
In-progress resumable upload (tus, API_CONTRACT §7). On completion the partial becomes the stored original and a `media_item`, and this row goes `completed`. Transient — not part of the durable model.

### derivative
```sql
derivative (
  id            uuid pk,
  media_item_id uuid not null fk -> media_item(id),
  kind          text not null,             -- 'thumb' | 'preview'
  storage_key   text not null,             -- ALWAYS on Pi (thumbnail-byte cache, §6.3)
  width         int not null,
  height        int not null,
  cached_at     timestamptz not null,      -- for google_photos: when fetched from Google
  unique (media_item_id, kind)
)
```
In convenience mode the original lives in Google Photos but the thumbnail bytes are cached here, so repeat gallery views don't hit Google's 75k byte-quota.

### share_token
```sql
share_token (
  id          uuid pk,
  event_id    uuid not null fk -> event(id),
  token_hash  text not null unique,        -- sha256 of the opaque link secret
  scope       text not null,               -- 'contribute' | 'view' | 'host'
  passcode_hash text null,                 -- sha256 of the host passcode; set only for scope='host'
  label       text null,                   -- e.g. 'main link', 'view-only'
  expires_at  timestamptz null,
  revoked_at  timestamptz null
)
```
The raw token is never stored — only its hash. Multiple tokens per event (a contribute link + a separate view-only link, rotation). See §4.

### google_account
```sql
google_account (
  id                      uuid pk,
  email                   text not null,   -- the admin's account; for display only
  encrypted_refresh_token bytea not null,  -- encrypted at rest; app-level key
  scopes_granted          text not null,   -- e.g. 'photoslibrary.appendonly ...'
  status                  text not null default 'active', -- 'active' | 'revoked'
  last_refreshed_at       timestamptz null
)
```
**One row per instance — the admin's** Google account, referenced by convenience-mode events on this instance. Refresh token is the long-lived credential — encrypted at rest, revocation handled gracefully (PRODUCT_SPEC §6.4). Privacy-mode events never use it.

### google_deletion
```sql
google_deletion (
  id                 uuid pk,
  event_id           uuid null,            -- denormalized; no FK (outlives the event)
  google_media_id    text not null,        -- the Google item the admin must delete by hand
  google_product_url text null,            -- direct Google Photos link
  kind               text not null,        -- 'photo' | 'video'
  captured_at        timestamptz null,
  deleted_at         timestamptz not null,
  cleared_at         timestamptz null      -- admin marked it handled
)
```
Convenience-mode items deleted in PartySnap that remain in the admin's Google Photos (§7) — the admin's manual-cleanup checklist before sharing the album.

## 4. Capability tokens (the share link)

**Format: opaque random, not JWT.** A 128-bit URL-safe random string (`/e/{token}`), stored only as `sha256(token)` in `share_token.token_hash`. Chosen over signed/JWT because:
- **Revocation is a row update** (`revoked_at`), not key rotation or a blocklist.
- No signing-key management, no alg-confusion footguns.
- The link is already a bearer secret; a random opaque string is the honest representation.

**Validation:** incoming token → hash → lookup → reject if missing, `revoked_at` set, or past `expires_at`. Otherwise yields `(event_id, scope)`.

**Scopes:**
| scope | can | typical use |
|---|---|---|
| `contribute` | view + upload | the main guest link (pure bearer) |
| `view` | view only | share the album read-only (pure bearer) |
| `host` | manage the event: rename, remove items, delete event, export, rotate the contribute/view links | the event's management credential — **gated by a passcode** (see below) |

**Host gating:** unlike `contribute`/`view`, a `host` link is inert without its **passcode**. The passcode is human-typed (entered on another device), so it's hashed with **argon2** (a slow KDF), not sha256 — stored in `share_token.passcode_hash`. Validating a host action requires *both* the hashed token and the passcode to match; the argon2 cost is paid only at session-exchange, not per request. Co-hosts share one host link + passcode and are indistinguishable. **Instance-level config — storage backend, Google OAuth, the instance Google account — is the admin's, not the host's** (§1).

**Rotation:** issue a new token, set `revoked_at` on the old → the old link dies, the new one works. For the `host` credential, rotate the passcode alongside it.

## 5. Device tokens (returning contributor)

- On **join**, the server issues the device a fresh 128-bit secret, stores `sha256` as `participant.device_token_hash`, returns the raw secret once. The client persists it (secure storage).
- Subsequent requests send it as a bearer header → identifies the `participant` → enables "my uploads," attribution, and self-delete of own items.
- Accountless: no email/PII/password, but a **required** `display_name`, collected at join — attribution depends on it. The device token itself carries no identity; the name is the human-facing attribution.
- Lost device secret = a new participant (their old uploads stay, just no longer "theirs" to manage). Acceptable tradeoff.

## 6. Host management & cross-device

The **gated `host` link + passcode** *is* the event's management credential — created with the event, grants full management of that event. Cross-device is uniform: open the host link on any device, enter the passcode. No "trusted device," no device-held session; the link is something-you-have, the passcode something-you-know, both required together. Co-hosts share the one credential.

**Admin vs host.** Instance-level management — all events, storage backend, Google OAuth, the instance Google account — is the **admin's**, via the operator login (§1): a separate, durable per-instance credential set at instance setup. The host credential is per-event and capability-based; the admin login is per-instance and durable. In a personal deployment one person holds both, but the two are distinct.

**Rejected alternatives (and why):** an **email magic-link host account** — needs outbound mail infrastructure, which contradicts the self-hosted, no-third-party thesis; **ferrying the raw host token** across devices — exposes a durable secret over chat/email. The passcode-gated link sidesteps both.

## 7. Deletion semantics (ties to PRODUCT_SPEC §6.2)

**The guarantee is asymmetric by mode** — deletion fully scrubs Pi-side data, but in convenience mode the Google Photos copy is **not** deleted: the `appendonly`/`appcreateddata` scopes can't delete, and the bytes live in the admin's own library by design (PRODUCT_SPEC §6.1).

- **Privacy mode:** delete = complete. Originals + derivatives gone from the Pi; nothing else held them.
- **Convenience mode:** delete removes PartySnap's copy (cached derivatives, metadata, album reference); the **originals persist in the admin's Google Photos** until the admin removes them by hand.

Deleting an event:
1. **Blobs purged immediately** — Pi originals + derivatives deleted from disk. Convenience mode also drops cached derivatives + the album reference; Google originals remain (above).
2. **Rows soft-deleted** (`status='deleted'`, `deleted_at` set), then **hard-purged after the backup-rotation window** (~7 days, §6.2) by a background worker, so no DB backup outlives the deletion promise.

Self-delete of a single item: same blob-purge, immediate row removal.

**Convenience-mode deletion record + album sharing.** Since the Pi can't delete from Google, every deleted item that had reached Google is logged in `google_deletion` (with its `productUrl`), surviving the media/event rows. The admin's wrap-up: at event end, share the Google Photos **album** natively from their own library — after pulling the record (`GET /admin/google/deletions`), removing those items by hand, and clearing it (`POST /admin/google/deletions/clear`). PartySnap maintains the album (the relay appends to it); native Google sharing + this record are how the admin curates the album and hands it to guests.

## 8. Resolved design decisions

- **`storage_backend` immutability** — **accepted.** An event's mode is fixed at creation; switching means creating a new event (no in-place byte migration).
- **Dedup scope** — **confirmed.** `unique(event_id, checksum)` dedups within an event; the same photo in two events is stored twice, intended (events are isolated).
- **Approval join policy** — deferred. When built, `join_policy='approval'` needs a `pending` participant state not yet modeled; noted for when moderation lands.
