from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Instance config, sourced from the environment (DEPLOYMENT.md §3)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    app_secret: str = "dev-insecure-change-me"
    partysnap_domain: str = "localhost"

    media_root: str = "/app/media"
    per_guest_cap_bytes: int = 100 * 1024**3  # ~100 GB anti-abuse cap (PRODUCT_SPEC §5)
    google_video_max_bytes: int = 20 * 1024**3  # convenience-mode per-clip ceiling (§6.3)

    # Admin bootstrap — env-seeded, single-use (DEPLOYMENT §5).
    admin_handle: str | None = None
    admin_password: str | None = None

    # Convenience mode (optional). Absent => privacy-mode-only instance (DEPLOYMENT §7).
    google_client_id: str | None = None
    google_client_secret: str | None = None

    @property
    def google_oauth_redirect_uri(self) -> str:
        # Derived from the domain (single source). Register this exact URL on the
        # Cloud Console OAuth client (DEPLOYMENT §7).
        return f"https://{self.partysnap_domain}/api/admin/google/oauth/callback"

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


settings = Settings()
