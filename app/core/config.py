from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Channel
    twilio_account_sid: str
    twilio_auth_token: str
    # Own Twilio number, connected to a Meta Business Account - no longer the
    # shared sandbox (+14155238886). No join code, no 3-day join expiry, and
    # custom templates can be created instead of the sandbox's fixed three -
    # but the 24h free-form-message window is Meta's rule, not the sandbox's,
    # and still applies here.
    twilio_whatsapp_from: str = "whatsapp:+13158126378"
    # Content API SID (starts 'HX') for the approved "appointment reminder"
    # template - §03 §3.4. Optional so a missing value degrades (out-of-window
    # reminders log and skip) rather than crashing the app; look it up via
    # GET /internal/debug/templates.
    twilio_appointment_template_sid: str | None = None
    # Seeds one is_emergency=true contact for the demo (§09 seed step) so the
    # SOS "Done when" check has a real second phone to alert without a vCard
    # upload first. Optional; seed.py skips contact seeding when unset.
    demo_emergency_contact_phone: str | None = None

    # AI
    gemini_api_key: str
    gemini_model_fast: str = "gemini-flash-lite-latest"
    gemini_model_main: str = "gemini-flash-latest"

    # Google
    google_client_id: str
    google_client_secret: str
    google_redirect_uri: str

    # Data
    database_url: str
    media_root: str = "/data/media"
    tts_cache_root: str = "/data/tts"

    @field_validator("database_url")
    @classmethod
    def _force_asyncpg_driver(cls, v: str) -> str:
        # Railway's Postgres plugin (and most providers) hand out a plain
        # postgres:// or postgresql:// URL, which SQLAlchemy maps to the sync
        # psycopg2 driver. The app uses the async engine, so force asyncpg.
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    # Crypto
    token_encryption_key: str
    media_signing_key: str
    admin_token: str

    # App
    public_base_url: str
    default_timezone: str = "Asia/Singapore"
    log_level: str = "INFO"
    environment: str = "development"
    # Judge-facing demo path (see README "DEMO_MODE"): skips the caregiver
    # onboarding/consent chain entirely and auto-provisions a fully-populated
    # patient on first contact from an unknown number. Every demo-only branch
    # in the codebase is gated on this one flag; everything else is
    # unchanged when it's False (the default).
    demo_mode: bool = False

    @property
    def sync_database_url(self) -> str:
        # APScheduler's SQLAlchemyJobStore uses sync SQLAlchemy internally and
        # can't share the asyncpg engine - only the job store needs this.
        return self.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)


settings = Settings()
