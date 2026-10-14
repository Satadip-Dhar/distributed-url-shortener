from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # App
    APP_NAME: str = "URL Shortener"
    BASE_URL: str = "http://localhost:8000"
    SHORT_CODE_LENGTH: int = 7

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@db:5432/urlshortener"
    DATABASE_READ_URL: str = "postgresql+asyncpg://postgres:postgres@db-replica:5432/urlshortener"

    # Redis
    REDIS_URL: str = "redis://redis:6379/0"
    CACHE_TTL_SECONDS: int = 3600  # 1 hour

    # Redirect
    DEFAULT_REDIRECT_STATUS: int = 302  # 302 = trackable, 301 = browser-cached

    class Config:
        env_file = ".env"


settings = Settings()
