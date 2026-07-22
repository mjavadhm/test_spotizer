from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./spotizer.db"

    # Client auth: "telegram:key1,bale:key2"
    CLIENT_KEYS: str = ""

    # Downloads
    TEMP_DIR: str = "/tmp/spotizer"
    FILE_TTL_MINUTES: int = 30
    MAX_CONCURRENT_DOWNLOADS: int = 2
    DEEZER_ARL: str = ""

    # Track library (permanent storage used by /tracks stream & download)
    MUSIC_DIR: str = "./music"
    # how long a stream/download request waits for an on-demand download
    ON_DEMAND_TIMEOUT_SECONDS: int = 120

    # Optional: Google Gemini key for /recommendations (empty = feature disabled)
    GEMINI_API_KEY: str = ""

    API_V1_PREFIX: str = "/v1"
    DEBUG: bool = False

    def client_key_map(self) -> dict[str, str]:
        """Returns {api_key: platform}."""
        mapping: dict[str, str] = {}
        for pair in self.CLIENT_KEYS.split(","):
            pair = pair.strip()
            if ":" in pair:
                platform, key = pair.split(":", 1)
                mapping[key.strip()] = platform.strip()
        return mapping


@lru_cache
def get_settings() -> Settings:
    return Settings()
