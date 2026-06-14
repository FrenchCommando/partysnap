"""Set up a fresh instance: create the schema, then env-seed the admin.

No migration tool (DEPLOYMENT §9): in development a schema change means dropping
the `pgdata` volume and re-running this. `create_all` only ever *creates missing*
tables — it never alters existing ones, so a change to a table that already holds
real data needs a hand-applied ALTER, not this script.

Admin seeding (DEPLOYMENT §5) lives here rather than at app startup so it runs
once, at the schema step, after the tables exist — not on every boot.
"""

import asyncio

from sqlalchemy import select

from app import models
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.security import hash_secret


async def _seed_admin() -> None:
    if not (settings.admin_handle and settings.admin_password):
        return
    async with SessionLocal() as session:
        exists = (
            await session.execute(select(models.Admin).limit(1))
        ).scalar_one_or_none()
        if exists is not None:
            return  # idempotent — never re-seed once an admin exists
        session.add(
            models.Admin(
                handle=settings.admin_handle,
                password_hash=hash_secret(settings.admin_password),
                must_change_password=True,
            )
        )
        await session.commit()
        print(f"seeded admin '{settings.admin_handle}' (must change password on first login)")


async def main() -> None:
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:
        raise SystemExit(
            "ERROR: cannot connect to the database — check POSTGRES_USER / "
            f"POSTGRES_PASSWORD / POSTGRES_DB in .env.\n  {exc}"
        )
    await _seed_admin()
    print("schema ready")


if __name__ == "__main__":
    asyncio.run(main())
