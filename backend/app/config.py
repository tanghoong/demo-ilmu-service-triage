from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All secrets live here, on the server. The browser never sees any of it."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ilmu_api_key: str = ""
    ilmu_base_url: str = "https://api.ilmu.ai/v1"
    ilmu_model: str = "ilmu-v3.1"
    ilmu_timeout_seconds: float = 25.0
    ilmu_max_retries: int = 2
    ilmu_mock: bool = False

    allowed_origins: str = "http://localhost:5173"
    rate_limit_per_minute: int = 20
    audit_db_path: str = "./audit.db"
    # Retain the model's summary and draft reply. On for the demo so runs can
    # be compared; off in production, where flags and queue are the record.
    audit_store_content: bool = True

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def use_mock(self) -> bool:
        # Fall back to the stub automatically when no key is configured, so the
        # demo is never blocked on credentials.
        return self.ilmu_mock or not self.ilmu_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
