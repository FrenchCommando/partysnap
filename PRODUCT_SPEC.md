# PartySnap — Product Spec

> Status: Draft · Owner: Martial · Derived from [VISION.md](./VISION.md)

## 1. Problem

At any shared event — a wedding, a weekend trip, a skydiving jump — the best
photos are scattered across everyone's phones. The host ends up chasing guests
in a group chat, and half the pictures never surface. Existing options each fail
one way or another:

- **Group chats / AirDrop** — lossy, compressed, no single collection, falls apart past a handful of people.
- **Shared Google Photos album** — requires everyone to have an account and opt in, buries the event among personal libraries, and hands all the data to Google.
- **Wedding-specific apps** — single-occasion, often paywalled per event, and overkill for a camping trip.

**PartySnap** is one shared photo/video bucket per event. Guests join in seconds
via a link or QR code, drop their media in, and the host owns the whole
collection. No account required to contribute. Privacy and host ownership are the
point, not an afterthought.

## 2. Goals & non-goals

### Goals
- Guest can contribute photos/videos in **under 30 seconds from a cold start** (tap link → grant access → upload), no signup.
- Host can create an event and share it in **one screen**.
- Originals preserved — **no silent recompression** of uploads.
- The host can **export or delete the entire collection** at any time. Data ownership is explicit.
- Works for the long tail: 3-person trip up to a ~200-guest wedding.

### Non-goals (for now)
- Not a photo *editor* (filters, retouching).
- Not a social network — no public feeds, likes, comments, followers.
- Not a permanent cloud archive / Google Photos replacement for personal libraries.
- No face recognition or auto-tagging for now (revisit; privacy-sensitive — see §8).

## 3. Target users & personas

| Persona | Role | Need |
|---|---|---|
| **Admin** (instance operator) | Runs the Pi/instance; owns the deployment, storage/OAuth config, and the instance Google account | Stand the box up once; let hosts run events on it without touching infra. |
| **Host** (Maya, organizing a wedding) | Creates event, curates, owns the result | Collect every guest's photos in one place without nagging; download originals after. |
| **Guest** (Tom, wedding attendee) | Contributes, browses | Zero-friction upload; doesn't want a new account or app cruft. |
| **Viewer** (relative who couldn't attend) | Browses, downloads | See the album, save favorites. |

A single event has one or more hosts (co-hosts share one gated host credential —
link + passcode) and many guests; a guest may also be a pure viewer. The
**admin** runs the instance the event lives on — a distinct role, though in a
personal deployment the same person.

## 4. Core user flows

### 4.1 Host creates an event
1. Open app → "New Event".
2. Name it, pick a cover, set start/end window (optional), set join policy (open link vs. approval).
3. Get a **share link + QR code**. Done.

### 4.2 Guest joins and uploads
1. Tap link / scan QR → opens app or web fallback.
2. Enter a display name (required — attribution depends on it). No account, no password.
3. Grant photo/camera access → either snap live or pick from camera roll.
4. Upload runs in background with progress; originals preserved.

### 4.3 Browse & download
- Unified event gallery, reverse-chronological, grouped by day.
- Per-item: who uploaded, timestamp. Tap to view full-res; download single or bulk.

### 4.4 Host wrap-up
- Host can **bulk-export** the collection: zip / save-to-device. In convenience mode the durable archive already lives in the **admin's** Google Photos (see §6, §6.1).
- Host can **delete the event**, which purges all media. Surfaced as the privacy guarantee.

## 5. Features

### MVP
- Event create / join via link + QR.
- Accountless-but-named guest contribution (required display name, lightweight device identity, no password).
- **Guests contribute via the app only**; web is view-only (viewers) plus host/admin management.
- Photo **and** video upload, original quality, background + resumable. **Per-guest anti-abuse cap (~100 GB total)** — set deliberately high; a normal contributor never approaches it, it only stops a runaway/malicious uploader. Not a quota the guest manages. Enforced server-side as cumulative bytes per participant (size metadata only, no content inspection); no per-file or per-event cap.
- Event gallery: grid, full-screen viewer, per-item attribution, day grouping.
- Single + bulk download.
- Host controls (via gated host link + passcode; co-hosts share it): rename event, remove an item, delete whole event, rotate links.
- Admin controls (operator login): instance setup, choose/enable storage backend, connect the instance Google account; spans all events.
- Capacity/quota indicator.

### Later
- In-app camera (capture straight into the event).
- Join policy = approval queue; host moderation.
- Per-host co-host identity (distinguishable, individually revocable) — beyond today's *shared* host credential.
- **Export to Google Photos** — push a privacy-mode collection into the admin's library/album as a durable archive (see §6.1).
- "Attach to event" deep-link integration — **Partiful, Google Calendar event, generic URL** (the vision's integration hook).
- Albums / sections within an event (e.g. "Ceremony", "Reception").
- Favorites / lightweight reactions.
- Slideshow / live-wall mode for a venue screen.

### Exploratory
- On-device face grouping (privacy-preserving, opt-in).
- Self-hosted / bring-your-own-bucket storage backend.

## 6. Storage & privacy model

Privacy and host ownership are the core differentiator, so the storage model is a
product decision, not just an infra one.

- **Event-scoped buckets.** Each event's media lives in its own logical container, isolated from any guest's personal library and from other events.
- **Access by capability, not identity.** The share link/QR *is* the credential to contribute and view. No requirement that guests have accounts. Host can revoke/rotate the link.
- **Originals retained**; the app generates thumbnails/preview derivatives but never replaces the source file.
- **Storage layer is pluggable, chosen per event.** The Pi always owns the metadata, identity, and capability links — the parts no third party can do. *Where the bytes live* is a per-event backend behind one interface (`put / get-url / delete`), so the same app and schema support both modes below. This is the `storage_backend` discriminator the data model carries from day one.

### 6.1 Two live-store modes

The collection flow (accountless guest upload via a capability link) is identical in both modes; only the blob backend differs. The host picks per event.

**Who needs a Google account:** only the **admin** (instance operator), and only in **convenience mode** — because the bytes land in the admin's own Google Photos, so the admin grants the OAuth consent once at instance setup. **Guests, viewers, and hosts never OAuth and never need a Google account, in either mode.** Privacy mode involves Google for nobody. The OAuth grant is authorization to write to Google Photos, *not* a login to PartySnap — the identity systems are separate. The admin decides which modes the instance offers; the host picks per event among those; it never reaches the guest, so the "<30s, no signup" guest goal holds regardless.

**Privacy mode — blobs on the Pi**
- Media originals live on the admin's Pi (the instance); nothing touches a third party. This is the vision's core pitch.
- **Deletion is real deletion** — purging an event removes originals + derivatives within a stated window (subject to the backup-rotation policy, §6.2).
- Costs the admin: Pi disk, and home **upload** bandwidth when viewers browse (the Pi serves every view).

**Convenience mode — Google Photos as the live store**
- The admin authorizes PartySnap once with `photoslibrary.appendonly`; guest uploads are relayed (using the admin's token) into the admin's library and an event album. Guests never see Google.
- The gallery renders from **app-created** items read back via the API; viewers fetch bytes from **Google's CDN** via each item's `baseUrl`, so the Pi serves no media — home upload bandwidth is out of the path. Pi holds only metadata + the admin's token.
- Confirmed against Google's March 2025 API changes: append/upload and app-created read-back survive; the broad read/manage and `sharing` scopes were removed.
- Trade-offs the admin accepts: bytes sit on Google from upload #1 (privacy conceded), uploads count against the admin's Google **storage quota** at original quality, and the gallery hard-depends on the admin's OAuth token staying valid. `baseUrl`s expire (~60 min) so the Pi must refresh + cache them, which puts gallery views on Google's **API quota**. Album sharing isn't available via API — within PartySnap the app *is* the viewer. But the per-event **album** is also the convenience-mode wrap-up artifact: at event end the admin can **share the album natively from their own Google Photos**, after curating it via the deletion record (DESIGN §7) to remove items deleted in PartySnap.
- Deletion: PartySnap removes its metadata and the album; the bytes remain in the admin's own Google Photos for the admin to manage. The purge guarantee covers Pi-side data only.

See §6.3 (quota) and §6.4 (OAuth) for the two constraints that most shape convenience mode.

### 6.2 Backups & deletion (privacy mode)

*Decision pending — see §8.* Off-Pi DB backups protect metadata against SD/power-loss corruption, but a backup taken before an event is deleted still holds that event's metadata, which weakens "real deletion." Leaning: **DB-only backups with short rotation (~7 days)**, media not backed up beyond the host's own export → deletion is real within the rotation window. Media-blob backup, if added, inherits the same rotation rule.

### 6.3 Storage quota (convenience mode)

The cost that convenience mode shifts onto the host, and the limit that caps event size.

- **Whose quota:** API uploads consume the **admin's** Google account storage, at **original quality** — there is no free "storage-saver" tier via the API. A media-heavy wedding (esp. GoPro video) can exhaust the free **15 GB** fast; the admin effectively needs **Google One** for large events. Surface an estimated-size warning before enabling convenience mode for an event.
- **Per-request limits (legacy upload path, still current):** uploads are byte-stream → `mediaItems.batchCreate`, **≤ 50 items per batchCreate call**; the app must chunk. Individual file size limits: photos up to ~200 MB, **videos up to ~20 GB**. PartySnap caps total bytes per guest (100 GB, §5), not per file — so a single clip can be up to that whole budget, but in convenience mode Google still rejects any clip over ~20 GB even though the Pi accepted it; validate per-file against Google's ~20 GB ceiling client-side in convenience mode.
- **API request quota — two separate per-project/day pools** (verified, current):
  - **Library API: 10,000 req/project/day** — uploads, list, search, and fetching item metadata (which mints a `baseUrl`). *Not* pixel loads.
  - **Media byte access: 75,000 req/project/day** — loading the actual bytes from a `baseUrl`.
  - Over either → HTTP **429**; back off. Increases requestable via the API Console.
- **Both pools are project-level**, shared across *all* events on the instance (one OAuth project per instance) — they do **not** scale the way Pi storage does. This is the central scaling limit of convenience mode.
- **The 10k pool is the dangerous one — uploads and `baseUrl` minting share it.** Exhausting it returns 429 on *uploads*, not just reads. That's the worst failure: a missed view is annoying, a missed upload loses the moment permanently. So the upload path must never depend on Google quota (see ingest model below).

#### Ingest model — Pi-first, Google-async (convenience mode)

Convenience mode does **not** upload straight to Google. The Pi is the source of truth at capture; Google is an async replication target for durability + read-bandwidth offload.

1. **Guest upload → Pi accepts and stores the original locally, acks immediately.** The upload is complete from the guest's view; it never touches Google synchronously.
2. **Pi relays to Google Photos in the background**, via a rate-limited queue kept under the 10k pool. If quota is exhausted, the queue waits and drains after the daily reset (midnight PT). Retries are invisible.
3. **After Google confirms an item, the Pi prunes the original**, keeping only the thumbnail + a bounded **preview** (~2048px). Before confirmation it holds the full original.

Storage consequence: convenience mode is **not** zero-storage during an event — the Pi transiently holds each original until relayed (a relay backlog at a live event is a real, draining pile), then settles to thumbnail + preview per item.

#### Serving & graceful degradation

- **Thumbnails + previews are always Pi-served** (cached bytes), hitting **zero** Google quota. Google's CDN is touched only when a viewer opens a **true original**.
- So normal browsing barely consumes either pool. The fallback only has to cover original-quality fetches.
- **Monitor and degrade:** the 10k pool is **exactly** observable (the Pi makes those calls); the 75k pool clients hit Google directly, so the Pi only **estimates** it (or polls Cloud Monitoring). At ~80% of either pool, **serve the Pi-cached preview in place of the original** — resolution dips, nothing 429s. Recovers at the daily reset.
- **Net:** uploads are bulletproof regardless of quota; reads degrade preview-only near the cap. Heavy multi-host adoption still eventually needs a Google quota increase, or it's a reason to keep some events in privacy mode.

### 6.4 OAuth & app verification (convenience mode)

The constraint that decides whether convenience mode is "just for me" or "shippable."

- **There is no API-key-only path.** The Photos Library API acts on a user's *private* library, so it mandates **OAuth user consent** — the admin must, in **Google Cloud Console**, create a project, enable the API, create OAuth credentials, and configure an **OAuth consent screen**. "Testing/Production" below is that consent screen's **publishing status** (a Cloud Console setting on the admin's project), *not* a tier of the API or anything in our code.
- **Scope class:** `photoslibrary.appendonly` (and the app-created read scope) are **sensitive/restricted** scopes. Any app requesting them is subject to Google's OAuth policies.
- **Testing vs Production publishing status** (of the OAuth consent screen):
  - **Testing** — works immediately, no review, but: capped at **100 test users** (each added by email), the consent screen shows an "unverified app" warning, and crucially **refresh tokens expire after 7 days**. Because only the **admin's single account** OAuths per instance, the 100-test-user cap is a **non-issue** for a self-hosted instance — the admin adds themselves and that's the only consent. The real friction is the **7-day refresh expiry**: the admin re-auths weekly (or the app re-prompts). That's per-instance, so it stays tolerable even when distributing the app to other admins.
  - **Production** — refresh tokens are durable and the cap is lifted, but requires passing Google's **OAuth verification**, and for restricted scopes a **third-party security assessment** (CASA) that is slow and can carry real cost. **PartySnap never needs this** — see the distribution model below.
- **Headless OAuth (the Pi has no screen):** the consent UI is **Google's, shown in the admin's own browser** (laptop/phone) — never on the Pi. Flow: admin taps "Connect Google" → Pi builds the consent URL → admin approves on Google's page → Google redirects with a one-time `code` to the Pi's **own public endpoint** `https://<domain>/api/admin/google/oauth/callback` (the Pi already terminates TLS on its domain, §7) → Pi exchanges the code server-to-server for the refresh token and self-refreshes thereafter. **No SSH, no commands on the Pi, no GUI on the Pi.** One-time setup (from a browser, not the Pi): register the OAuth client in Google Cloud Console and drop `client_id`/`secret` into the Pi's deploy config. Weekly re-consent in Testing mode is the same one-tap browser approval.
- **Access survives re-consent (tied to `client_id`, not the token).** App-created data — the albums + items the relay made — is owned by the OAuth **client**, not by any token or session. So the weekly Testing-mode re-consent (new refresh token), and even a full revoke→re-grant, keep access to every previously-created album: the relay keeps appending across re-auths. The *only* thing that orphans it is changing `GOOGLE_CLIENT_ID` (a new OAuth client = a different app identity) — keep it stable.
- **Token storage:** the admin's refresh token is a long-lived credential the Pi must store **encrypted at rest** and be able to revoke. Token revocation by the admin (from their Google account) must be handled gracefully — the gallery degrades, it shouldn't crash.
- **Expiry visibility (display, not nag):** the app does **not** prompt or remind. Instead the admin UI **always shows the refresh token's expiry date**, with escalating visual urgency as it nears — neutral when fresh, warning colors as it approaches, alarming/red styling when imminent or lapsed. The admin chooses when to re-consent (one-tap reconnect). Uploads keep working regardless of expiry (Pi-first, §6.3); the display just flags when the Google relay will stall.
- **Distribution model — why verification is never a launch gate:** PartySnap ships as **self-hosted software**, not a hosted multi-tenant service. Each deployment is a **new admin + new hardware + new Google account, with its own Google Cloud project + OAuth client**. So there is never one shared OAuth app serving many users — each instance's OAuth app has exactly **one user, its own admin**, and stays in **Testing** forever. Therefore **Google verification/CASA never sits on PartySnap's path** — not for personal use, not for "launching." Whatever the developer does with their own Google account is irrelevant to any other instance. The cost moves to **onboarding**: each admin does the one-time Cloud Console setup (project, OAuth client, consent screen) themselves — a real burden for non-technical admins, and the price of the self-hosted/no-third-party model. (A future hosted option, §8 #1, would have to absorb this setup for them.)
- **Design consequence:** **privacy mode needs no OAuth at all** and ships freely. Convenience mode works immediately on any admin's own instance (Testing, weekly re-consent surfaced via the expiry display above) with **no verification ever required**, because each instance is a sole-user OAuth app. Privacy mode stays the safe default; convenience mode is per-event opt-in. Reinforces shipping the storage interface with privacy mode first.

## 7. Technical shape (deployment)

Self-hosted on the host's own Raspberry Pi. No BaaS, no third-party storage.

- **Client:** Flutter (iOS + Android from one codebase; matches the stack Martial is learning). Web fallback for the join link is desirable but can be a thin "open in app / view-only" page to start.
- **Backend:** a small **FastAPI** service (Python) — device identity, metadata, upload/download endpoints — with **Postgres** for metadata and a media directory for blobs. Storage sits behind an interface (§6) so local-disk / cloud bucket are swappable.
- **Packaging:** API + Postgres run in **Docker** (compose), each with its own DB/user, Postgres bound to localhost only. Self-contained, version-isolated from the Pi's other apps, trivially relocatable to an SSD or another box.
- **Edge:** the Pi's existing **nginx** terminates TLS on the custom domain and reverse-proxies to the container as another `server` block. No tunnel needed — reachability and certs are already solved. nginx also streams media (good at large files).
- **Storage media:** the reference instance runs on its **235 GB SD card** (`mmcblk0`, ~209 GB free) — no SSD. Capacity is ample; the real cost is **SD write endurance** under a 24/7 write-heavy load, so the card is a **wear item** backed by off-Pi DB backups (§6.2), not durable storage. This shapes the mode choice: in **privacy mode the SD is the *only* copy of media originals** (media isn't backed up, §6.2) → prompt host export is the durability story; **convenience mode** keeps bytes on Google with only metadata + regenerable derivatives on the card, so card death is fully recoverable. A cheap external USB disk for the `media` volume would remove SD wear from the media path, if ever added. Upload bursts remain a heavy I/O neighbor for co-tenant apps.
- **Uploads:** background, resumable, chunked; preserve EXIF; never transcode originals. Generate thumbnails on-device or in a worker.
- **Identity:** three tiers (DESIGN §1) — guests get a device-scoped accountless token at join (named, no password); hosts use a gated link + passcode (shared by co-hosts); the admin has a durable operator login set at instance setup.

## 8. Resolved decisions

1. **Scaling past one Pi** — deferred. Self-hosting caps out at one admin's home upload bandwidth and uptime; a hosted/cloud option can arrive later *through the same storage interface*, which keeps the door open. Not a near-term concern.
2. **Storage caps** — a single **per-guest anti-abuse cap (~100 GB total)**, set high enough it's never reached in normal use; purely to stop a runaway uploader, not a quota. No per-file or per-event cap. Pi disk capacity is a separate concern, surfaced by the capacity indicator. In convenience mode a single clip is still subject to Google's ~20 GB/video ceiling (§6.3).
3. **Web contribution** — **guests contribute via the app only.** Web is view-only for viewers, plus host/admin management when opened with the right credential. The native app is the contribution surface; this also gives reliable background/resumable uploads that a browser (esp. iOS Safari) can't.
4. **Monetization** — deferred; no billing. The quota model stays host/admin-configurable so a paid tier *can* attach later without redesign.
5. **Moderation** — **host item-removal + link revoke/rotate** is enough. A pre-publish approval queue is deferred (would add a `pending` participant state, DESIGN §8).
6. **Face grouping** — deferred (stays a non-goal, §2). On-device + opt-in only if ever.

## 9. Success metrics (post-launch)

- Median guest time-to-first-upload.
- % of events with ≥2 contributing guests (validates the "collect everyone's photos" core).
- Photos per event; host export/download rate (did they get value out?).
- Event deletion working as promised (trust / correctness, not a vanity metric).
