# PartySnap — Deployment (Pi instance setup)

> Status: Draft · Companion to [PRODUCT_SPEC.md](./PRODUCT_SPEC.md) §7 (architecture) and [API_CONTRACT.md](./API_CONTRACT.md)
> Scope: standing up one self-hosted instance on a Raspberry Pi. Forward-looking — the compose file / image referenced here land with the backend scaffold; commands show the intended shape.

This is a runbook for **one admin standing up one instance** (DESIGN §1), in execution order. The goal is **zero interactive commands on the Pi after first boot** — everything ongoing happens from a browser on a normal device.

## 1. Prerequisites

- **Hardware:** Raspberry Pi 4 B (4 GB). **Storage:** the reference instance uses its **SD card** (235 GB, `mmcblk0`, ~209 GB free) for both `pgdata` and `media` — no SSD. Workable (capacity is ample), but the SD is a **wear item** — see the durability interaction in §8 and PRODUCT_SPEC §6.2/§7. An external USB disk for `media` is optional headroom, not required.
- **OS / kernel:** a **64-bit kernel** is required (`uname -m` = `aarch64`) — the userland may stay 32-bit (armhf). The reference instance runs a **64-bit kernel under a 32-bit userland**, so its other (non-PartySnap) projects keep running unchanged on their armhf images. On such a host, **pin PartySnap containers to `linux/arm64`** (§4): a 32-bit dockerd otherwise defaults to armhf. **Docker + Docker Compose** installed.
- **Already in place (PRODUCT_SPEC §7):** the Pi's **nginx** terminating **TLS on a custom domain**, reachable from the internet. PartySnap adds one `server` block; it does not manage DNS.
- **Optional (convenience mode only):** a Google account + the per-instance Google Cloud setup in §7. Skip entirely for privacy-mode-only instances.

## 2. Get the code

**Deploy model:** a **git checkout under the home dir** (`~/partysnap`) — `git clone` for the first deploy, `git pull` to update (§8); run everything from there. `.env` (§3) lives on the Pi, gitignored, created once. The only artifact that ever leaves the checkout is the nginx conf, copied into `/etc/` (§6).

```sh
git clone <repo-url> ~/partysnap      # explicit target → checkout is always at ~/partysnap
cd ~/partysnap
```

## 3. Configure `.env`

Instance config is declarative in a `.env` in the checkout, consumed by the compose stack. No secret is baked into the image.

**Quickest** — generate `.env` with the random secrets filled in:

```sh
PARTYSNAP_DOMAIN=snap.example.com APP_PORT=8080 ADMIN_HANDLE=martial bash scripts/init-env.sh
```

That writes `APP_SECRET`, `POSTGRES_PASSWORD`, and an initial admin password (printed once) for you; add the `GOOGLE_*` values by hand only if using convenience mode. Or write the whole file manually:

```dotenv
# --- core ---
PARTYSNAP_DOMAIN=snap.example.com           # the TLS domain nginx serves
APP_PORT=8080                               # required; HOST port nginx proxies to (any free port; container is always 8000 internally)
APP_SECRET=<32+ random bytes, base64>        # signs sessions + encrypts the Google token (DESIGN §3)

# --- database (container-internal) ---
POSTGRES_USER=partysnap
POSTGRES_PASSWORD=<random, URL-safe (hex)>
POSTGRES_DB=partysnap

# --- admin bootstrap (env-seeded; see §5) ---
ADMIN_HANDLE=martial
ADMIN_PASSWORD=<initial password>            # forced-changed on first login, then clear this line

# --- convenience mode (OPTIONAL; omit for privacy-only) ---
# The OAuth redirect URI is derived from PARTYSNAP_DOMAIN — not a var here.
GOOGLE_CLIENT_ID=<from Google Cloud, §7>
GOOGLE_CLIENT_SECRET=<from Google Cloud, §7>
```

Generate secrets off-Pi and paste them in. `.env` (from `.env.example`) is the **only** credential-bearing file — gitignored, lives only on the Pi. No secret is read from any other file.

**Credentials reference:**

| Var | What it is | Where it comes from | Format |
|---|---|---|---|
| `APP_SECRET` | Master secret — HMAC-signs sessions **and** derives the key that encrypts the Google refresh token at rest. | Generate yourself. | 32+ random bytes, base64. **Durable** — rotating it invalidates all sessions and makes the stored refresh token undecryptable (re-consent needed); see §9. |
| `POSTGRES_PASSWORD` | Postgres password (container-internal). | Generate yourself. | Random string. |
| `ADMIN_HANDLE` / `ADMIN_PASSWORD` | First admin login (env-seed, §5). | Choose. | Handle + initial password; `ADMIN_PASSWORD` is single-use, cleared after first login. |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | OAuth client for convenience mode. | The `client_secret_*.json` **downloaded from Google Cloud Console** (Credentials) — copy its `client_id` / `client_secret` fields. The app does not read the JSON. | Strings. Omit entirely for a privacy-only instance. |

`APP_PORT` is config, not a credential (**required** — the *host* port nginx proxies to; pick any free port). Note **`8000` is the container's fixed internal port** and appears only inside Docker — don't set `APP_PORT=8000`, keep the two distinct. The two `GOOGLE_*` vars are optional: absent ⇒ `google_configured` is false ⇒ privacy-mode only, no errors. The OAuth **redirect URI is derived** from `PARTYSNAP_DOMAIN` (`https://<domain>/api/admin/google/oauth/callback`) — register that exact URL on the Cloud Console client (§7), but it's not a `.env` var.

## 4. Bring up the stack

```sh
docker compose up -d --build          # API + Postgres (built from source), Postgres bound to localhost only
docker compose run --rm app python -m app.initdb   # create schema (DESIGN §3; create_all)
```

On a 32-bit-userland host (the reference instance), set **`platform: linux/arm64`** on each compose service so Docker pulls native arm64 images rather than armhf — the 64-bit kernel runs them natively.

Volumes: `pgdata` (Postgres) and `media` (blob originals + derivatives). On the reference instance these live on the **SD card** (no separate SSD). If you later add an external USB disk, move at least `media` onto it to spare the card.

## 5. Admin bootstrap (env-seeded)

- On first boot, if **no admin row exists**, the app seeds one from `ADMIN_HANDLE` / `ADMIN_PASSWORD`. Idempotent: it never re-seeds once an admin exists.
- **First login forces a password change** (`POST /admin/password`, API_CONTRACT §3) — the seeded password is single-use.
- **After first login, remove `ADMIN_PASSWORD` from `.env`** so the initial secret doesn't linger.

This is the only place a credential touches the deploy config, and it's short-lived by design.

## 6. nginx (reverse proxy + TLS)

PartySnap runs on its own subdomain as a `server` block in the existing nginx — wherever your other sites' configs live (commonly `/etc/nginx/conf.d/`).

The block is **rendered by `scripts/init-env.sh`** (§3) from `deploy/nginx/partysnap.conf.template`, filling `server_name` and the proxy port from `PARTYSNAP_DOMAIN` / `APP_PORT` — so the domain and port live in **one** place, never hand-edited here. It's **HTTP-only**; certbot adds the 443/TLS block to the *deployed copy*, keeping the rendered file clean. (Key directives, for reference: `proxy_request_buffering off` + uncapped `client_max_body_size` for tus uploads; `X-Forwarded-Proto` so the app builds https links + the OAuth redirect.)

Deploy — **copy, don't symlink** (so certbot edits the deployed copy, not the rendered file — a symlink would fight every re-render):

```sh
sudo cp ~/partysnap/deploy/nginx/partysnap.conf /etc/nginx/conf.d/
sudo certbot --nginx -d <your-domain>    # = PARTYSNAP_DOMAIN; adds 443/TLS + http->https redirect, reloads, manages renewal
```

certbot parses the config files to find the block (no prior reload needed). Changed the domain or port later? Re-render (`init-env.sh` style), re-`cp`, re-run `certbot --nginx`.

**Media serving — default is app-streams** (recorded default; flip if you'd rather): uvicorn serves media bytes after authorizing the request — simplest, no extra coupling. **Optimization for large originals — X-Accel-Redirect:** the app authorizes, then returns `X-Accel-Redirect: /_media_files/<storage_key>` and nginx streams the file off disk. Requires an `internal` `location /_media_files/ { alias …; }` **and** the `media` volume as a host bind-mount nginx can read. Documented as the upgrade path, not the starting default (PRODUCT_SPEC §7).

## 7. Convenience mode — Google Cloud setup (optional)

One-time, done **from a browser**, not the Pi (PRODUCT_SPEC §6.4). Per-instance, on the admin's own Google account:

1. In **Google Cloud Console**: create a project → enable the **Photos Library API**.
2. Configure the **OAuth consent screen** (External, Testing status — stays Testing; no verification needed, §6.4). Add yourself as a **test user**.
3. Create **OAuth client credentials** (type: **Web application**) → set the authorized redirect URI to `https://<PARTYSNAP_DOMAIN>/api/admin/google/oauth/callback` (the app derives this from the domain; §3).
4. Put the `client_id` / `client_secret` in `.env`, restart the stack.
5. In the PartySnap admin UI, tap **Connect Google** and approve in the browser. The expiry-date display (§6.4) tracks the 7-day Testing-mode re-consent.

Privacy-mode-only instances skip this section entirely.

## 8. Storage, backups, maintenance

- **Storage media:** Postgres + blobs on the **SD card** (§4 volumes; no SSD). Convenience mode holds each original only transiently before the Google relay prunes it (PRODUCT_SPEC §6.3) and is **durable-by-default** here (bytes on Google, card holds only metadata + regenerable derivatives). Privacy mode holds originals for the life of the event and the **card is their only copy** — un-exported media is lost if the card dies (PRODUCT_SPEC §6.2), so treat privacy mode as capture-and-export on this instance.
- **Backups:** automated **off-Pi DB backups, ~7-day rotation** (PRODUCT_SPEC §6.2) — protects metadata against SD/power-loss corruption without outliving the deletion promise. Media is not backed up beyond the admin's own exports.
- **Updates:** `bash scripts/deploy.sh` (from the checkout) — wraps `git pull` + `docker compose up -d --build` + the schema step (§4). Built from source on the Pi (arm64, §1) — no registry. Version-isolated from the Pi's other apps.
- **Relocation:** the stack is self-contained — move the SD card (or the volumes) to new hardware and bring the compose up there.

## 9. Open items

- **Backup tooling** — the exact off-Pi target (rsync to NAS / object store) is unspecified; rotation policy is fixed at ~7 days (§6.2).
- **`APP_SECRET` rotation** — re-encryption procedure for at-rest secrets is a runbook to write when the encryption layer lands.
- **Schema evolution — no migration tool (deliberate).** `create_all` creates missing tables but never `ALTER`s existing ones. In development a schema change = drop the `pgdata` volume + re-run the schema step. Once an instance holds real data, an additive change is a hand-applied one-line `ALTER` (a plain `.sql` kept in the repo); a destructive one is export → recreate → reimport. A migration tool (Alembic) is intentionally avoided for a single instance — revisit only if PartySnap is ever distributed to multiple instances, where per-instance hand-application doesn't scale.
