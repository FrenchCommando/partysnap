# PartySnap

One shared photo/video bucket per event. Guests join via a link or QR and upload
from the app; the host owns the whole collection. Self-hosted on a Raspberry Pi,
privacy-first, with optional Google Photos as the live store.

## Docs

- [VISION.md](./VISION.md) — the why.
- [PRODUCT_SPEC.md](./PRODUCT_SPEC.md) — problem, flows, storage/privacy model, decisions.
- [DESIGN.md](./DESIGN.md) — Postgres schema + the identity/auth model.
- [API_CONTRACT.md](./API_CONTRACT.md) — the REST surface.
- [DEPLOYMENT.md](./DEPLOYMENT.md) — standing up an instance on the Pi.

## Stack

FastAPI + Postgres in Docker Compose, behind the Pi's existing nginx. Flutter
client (planned). Backend lives in `backend/`.

## Quickstart (on the Pi)

```sh
git clone <repo-url> ~/partysnap && cd ~/partysnap
PARTYSNAP_DOMAIN=snap.example.com APP_PORT=8080 ADMIN_HANDLE=martial bash scripts/init-env.sh   # generate .env
bash scripts/deploy.sh                                                            # build + run + create schema
```

Then add the reverse-proxy block (`deploy/nginx/partysnap.conf`) and get a cert —
see [DEPLOYMENT.md](./DEPLOYMENT.md) §6.

## Two storage modes (per event)

- **Privacy** (default): originals live on the Pi; deletion is real. No third party.
- **Convenience** (optional): relayed to the admin's Google Photos; needs Google
  credentials ([DEPLOYMENT.md](./DEPLOYMENT.md) §7). Absent ⇒ privacy-only, no errors.
