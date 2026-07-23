from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Channel
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    # AI
    gemini_api_key: str
    gemini_model_fast: str = "gemini-flash-lite"
    gemini_model_main: str = "gemini-flash"

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


settings = Settings()
