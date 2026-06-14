# PartySnap — API Contract

> Status: Draft · Companion to [DESIGN.md](./DESIGN.md) and [PRODUCT_SPEC.md](./PRODUCT_SPEC.md)
> Scope: REST surface of the FastAPI backend. Derives directly from the entities in DESIGN §3 and the identity tiers in DESIGN §1.

## 1. Conventions

- **Base path:** `/api`. JSON request/response bodies (`Content-Type: application/json`), except media bytes and tus upload traffic.
- **IDs:** UUID strings. **Timestamps:** ISO 8601 UTC (`2026-06-13T14:00:00Z`).
- **Versioning:** unversioned path for now; negotiate via an `Accept`/version header later if needed (no `/v1` URL phase).
- **Errors:** standard HTTP status + FastAPI's default body `{ "detail": "human readable" }`.
- **Common statuses:** `401` missing/invalid credential · `403` valid credential, wrong scope · `404` not found / token doesn't resolve · `409` conflict (dedup) · `413` over the per-guest cap · `429` backend (Google) quota — server degrades to preview, see PRODUCT_SPEC §6.3.

## 2. Auth tiers & transport

All credentials ride as `Authorization: Bearer <token>`; the server resolves *which kind* by looking the token up. Four kinds (DESIGN §1):

| Tier | Obtained by | Bearer is | Scope |
|---|---|---|---|
| **Admin** | `POST /admin/sessions` with handle+password | short-lived admin session token | whole instance |
| **Host** | `POST /host/sessions` with host link token + passcode | short-lived host session token | one event |
| **Guest** (device) | `POST /join` with a `contribute` share token + name | durable device token (persisted client-side) | one event, own uploads |
| **Viewer** | a `view` share token | the share token itself (pure bearer) | one event, read-only |

- **Host session exchange** (chosen): the gated link + passcode are presented once to mint an ephemeral session token — not a durable "trusted device." Re-exchange when it expires.
- A request may carry only one Bearer; the resolved tier sets the permission ceiling. Admin ⊇ host capabilities on any event of the instance.

## 3. Admin (instance operator)

```
POST   /admin/sessions          { handle, password } -> { admin_token, expires_at, must_change_password }
DELETE /admin/sessions/current  -> 204                          # logout
POST   /admin/password          (admin) { current_password, new_password } -> 204   # first-login forced change (env-seeded bootstrap, DEPLOYMENT §5)
GET    /admin/instance          -> { storage_backends_available,
                                      google: { configured, connected, email?, status, token_expires_at? } }   # expiry drives the UI urgency display (PRODUCT_SPEC §6.4)
POST   /admin/google/oauth/start   -> { consent_url }           # begin OAuth (convenience mode)
GET    /admin/google/oauth/callback?code=...                    # Google redirect target; stores refresh token
DELETE /admin/google            -> 204                          # disconnect; convenience events degrade gracefully
GET    /admin/google/deletions       -> [ { id, google_media_id, google_product_url?, kind, captured_at?, deleted_at } ]  # items to remove by hand from Google before sharing the album (DESIGN §7)
POST   /admin/google/deletions/clear -> 204                     # "removed them" — mark all pending handled
GET    /admin/events            -> [ event_summary ]            # (PLANNED, not yet implemented) every event on the instance
```

## 4. Events

> Event creation is **admin-authed** (decided): the admin owns the instance, disk, and Google account, and creating an event mints the first host credential. In a personal deployment admin == host, so it's seamless; delegated hosts get their host link from the admin after creation.

```
POST   /events                  (admin)        { name, cover?, start_at?, end_at?, join_policy, storage_backend }
                                               -> 201 { event, host_link, host_passcode }   # passcode shown once
GET    /events/{id}             (host|admin|view) -> event
PATCH  /events/{id}             (host|admin)   { name?, cover_media_id?, start_at?, end_at?, join_policy? }
DELETE /events/{id}             (host|admin)   -> 202   # purge per DESIGN §7 (blobs immediate, rows soft->hard)
POST   /events/{id}/host-credential/rotate  (host|admin) -> { host_link, host_passcode }   # old link+passcode die
```

`event` body (implemented): `{ id, name, cover_media_id, start_at, end_at, join_policy, storage_backend, status }`. `counts: { media, participants }` and `capacity: { bytes_used }` are **planned**, not yet returned.

## 5. Share tokens (contribute / view links)

```
GET    /events/{id}/share-tokens   (host|admin) -> [ { id, scope, label, expires_at, revoked_at } ]
POST   /events/{id}/share-tokens   (host|admin) { scope: "contribute"|"view", label?, expires_at? }
                                                -> { id, scope, link }   # raw link shown once
POST   /share-tokens/{id}/revoke   (host|admin) -> 204
```

The `host` credential is not minted here — it is created with the event and rotated via §4.

## 6. Join (guest)

```
POST   /join                    { share_token, display_name } -> { device_token, participant_id, event }
```

- Resolves the event from the (`contribute`) share token; rejects `view`/expired/revoked.
- `display_name` is **required** (DESIGN §5). `device_token` is returned once; client persists it in secure storage.
- Re-join from the same device with the stored device token is a no-op identity refresh, not a new participant.

## 7. Media upload (tus.io resumable)

Uploads use the **tus 1.0** protocol (chosen). Guest auth = device token.

```
OPTIONS /uploads                (guest)   # tus discovery: Tus-Version / Tus-Extension: creation / Tus-Max-Size
POST   /uploads                 (guest)   # tus creation
        Headers: Tus-Resumable: 1.0.0, Upload-Length: <bytes>,
                 Upload-Metadata: kind <b64>, checksum_sha256 <b64>     (required)
                                  filename <b64>, filetype <b64>, captured_at <b64>  (optional)
        -> 201 Location: /uploads/{upload_id}, Upload-Offset: 0
PATCH  /uploads/{upload_id}      (guest)   # tus chunk; Upload-Offset, application/offset+octet-stream
HEAD   /uploads/{upload_id}      (guest)   # tus offset query for resume
```

The event is taken from the device token (the upload is scoped to that participant's event), **not** from `Upload-Metadata` — only `kind` + `checksum_sha256` are read from metadata.

- **Pre-checks at creation:** reject `413` if `Upload-Length` would push the participant over the per-guest cap (DESIGN §3 participant note); in convenience mode reject a single video over Google's ~20 GB ceiling (PRODUCT_SPEC §6.3).
- **Dedup:** `checksum_sha256` is checked against `unique(event_id, checksum)`. A duplicate **short-circuits creation**: `POST` returns **200** (not the tus `201`) with `{ media_item_id, duplicate: true }` and no upload resource is created — the client skips the transfer (PartySnap extension to tus creation).
- **On completion:** when the final `PATCH` brings the offset to `Upload-Length`, the server moves the partial to the stored original and creates the `media_item` (`status: processing`); the `204` response carries a **`PartySnap-Media-Id`** header naming it. Derivative (`thumb`/`preview`) generation runs after this and flips the item to `ready`; in convenience mode the Pi→Google relay (PRODUCT_SPEC §6.3) follows. So the item is **not yet `ready`** at the moment the `204` returns.

## 8. Gallery & media access

```
GET    /events/{id}/media?cursor=<c>&limit=<n>  (read*)
        -> { items: [ media_item ], next_cursor }       # ready items only; keyset on (coalesce(captured_at, created_at) desc, id)
GET    /media/{id}              (read*) -> media_item
GET    /media/{id}/thumb        (read*) -> bytes        # always Pi-served (cached)
GET    /media/{id}/preview      (read*) -> bytes        # always Pi-served (cached)
GET    /media/{id}/original     (read*) -> bytes | 302 -> Google baseUrl   # Pi (privacy) or CDN redirect (convenience)
DELETE /media/{id}              (uploader-self|host|admin) -> 204   # blob purge + row removal (DESIGN §7)
```

`* read` = admin, host session, device token, **or any share link (contribute/view) scoped to the event** (the resolver accepts any event-scoped credential for reads).

`media_item` body: `{ id, kind, mime_type, byte_size, width, height, duration_ms, captured_at, uploader: { participant_id, display_name }, status, urls: { thumb, preview, original } }`.

- **Degradation:** near a Google quota cap, `original` serves the Pi-cached preview instead of redirecting (PRODUCT_SPEC §6.3) — never a `429` to the client.
- Self-delete authorized by matching `device_token` → `uploader_id`; host/admin may delete any item.

## 9. Export (host wrap-up) — PLANNED, not yet implemented

```
POST   /events/{id}/export      (host|admin) { format: "zip" } -> 202 { job_id }
GET    /exports/{job_id}        (host|admin) -> { status, download_url? }
```

Streams originals as a zip (nginx-fronted for large files, PRODUCT_SPEC §7). Convenience-mode originals are pulled from Google for the archive.

## 10. Notes & remaining items

- **Event-creation authorization** (§4) — **resolved: admin-authed.**
- **Admin Google OAuth callback** — the `GET /admin/google/oauth/callback` is the one unavoidable browser redirect; the app launches it via system browser and catches the return.
- **Export job model** — async job (`202` + poll) assumed for large collections; a small event could stream synchronously. Job model chosen for consistency.
- **Byte-serving auth for web `<img>`** (§8) — thumb/preview/original authorize via the `Authorization` header, which the native app sends but a browser `<img src>` cannot. The web viewer needs a token-in-URL (signed, short-lived) or fetch-to-blob approach. Unresolved; lands with the web client.
- **Implementation status.** **Built:** §3 (admin sessions/password/instance/google + deletions), §4 events (create/get/patch/delete/rotate), §5 share tokens, §6 join, §7 uploads, §8 gallery + media + byte-serving. Plus `GET /api/health` (liveness + DB check), not listed above. **Planned, not yet implemented:** `GET /admin/events` (§3), `event.counts`/`capacity` (§4), and all of §9 export.
