import os
from functools import lru_cache
from typing import List, Optional
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server configuration
    APP_NAME: str = "Real-time OSINT Gathering Service"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    HOST: str = "0.0.0.0"
    PORT: int = Field(
        default=8000,
        validation_alias=AliasChoices("PORT", "HOST_PORT"),
    )
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "text"


    # Database configuration
    # By default, use SQLite async for zero-dependency local runtime, or PostgreSQL via env
    DATABASE_URL: str = Field(
        default="sqlite+aiosqlite:///./osint.db",
        description="Async SQLAlchemy database connection string",
    )
    DATABASE_URL_FALLBACK: str = "sqlite+aiosqlite:///./osint.db"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 5
    DB_POOL_TIMEOUT: int = 30

    # Redis bus & caching
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = True
    REDIS_FALLBACK_ON_ERROR: bool = True
    REDIS_RECONNECT_INTERVAL_SEC: int = 5

    # Security & Auth
    # Support both API_KEY and API_AUTH_SECRET environment variable names
    API_KEY: str = Field(
        default="osint_secret_key_2026",
        validation_alias=AliasChoices("API_KEY", "API_AUTH_SECRET"),
        description="Admin API Key for REST & WebSocket authentication",
    )
    ADMIN_PASSWORD: str = "admin123"
    PUBLIC_DISPLAY_PASSWORD: str = Field(
        default="osint-2026",
        validation_alias=AliasChoices("PUBLIC_DISPLAY_PASSWORD", "DISPLAY_PASSWORD"),
        description="Password required to access the public read-only display dashboard",
    )
    PUBLIC_DISPLAY_SECRET: str = Field(
        default="osint_viewer_secret_signing_key_2026",
        validation_alias=AliasChoices("PUBLIC_DISPLAY_SECRET", "VIEWER_SECRET"),
        description="Secret key for signing viewer session tokens",
    )
    CORS_ORIGINS: List[str] = ["*"]

    # AI Pipeline credentials
    GEMINI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    GROQ_API_KEYS: Optional[str] = None

    def get_groq_api_keys(self) -> List[str]:
        """Return parsed, deduplicated list of Groq API keys from config/env."""
        raw = self.GROQ_API_KEYS or self.GROQ_API_KEY or os.environ.get("GROQ_API_KEYS") or os.environ.get("GROQ_API_KEY")
        if not raw:
            return []
        items = [k.strip() for k in str(raw).replace("\r", "\n").replace(",", "\n").split("\n")]
        seen = set()
        out = []
        for k in items:
            clean = k.strip().strip("'\"")
            if clean and not clean.startswith("***") and clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out
    AIHUBMIX_API_KEY: Optional[str] = None
    AIHUBMIX_BASE_URL: str = "https://aihubmix.com/v1"
    AIHUBMIX_MAX_RPD: int = 100
    AIHUBMIX_MAX_TPD: int = 1_000_000

    # Mistral AI (La Plateforme Experiment / Free Plan)
    MISTRAL_API_KEY: Optional[str] = None
    MISTRAL_API_KEYS: Optional[str] = None
    MISTRAL_BASE_URL: str = "https://api.mistral.ai/v1"

    def get_mistral_api_keys(self) -> List[str]:
        """Return parsed, deduplicated list of Mistral API keys from config/env."""
        raw = self.MISTRAL_API_KEYS or self.MISTRAL_API_KEY or os.environ.get("MISTRAL_API_KEYS") or os.environ.get("MISTRAL_API_KEY")
        if not raw:
            return []
        items = [k.strip() for k in str(raw).replace("\r", "\n").replace(",", "\n").split("\n")]
        seen = set()
        out = []
        for k in items:
            clean = k.strip().strip("'\"")
            if clean and not clean.startswith("***") and clean not in seen:
                seen.add(clean)
                out.append(clean)
        return out

    # Cloudflare Workers AI (10,000 Neurons/day free)
    CLOUDFLARE_ACCOUNT_ID: Optional[str] = None
    CLOUDFLARE_API_TOKEN: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_KEY"),
        description="Cloudflare Workers AI API Token/Key",
    )
    CLOUDFLARE_API_KEY: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CLOUDFLARE_API_KEY", "CLOUDFLARE_API_TOKEN"),
        description="Cloudflare Workers AI API Token/Key",
    )
    CLOUDFLARE_BASE_URL: Optional[str] = None

    # Z.ai / Zhipu AI ($0 Flash models, unlimited RPD/TPD)
    ZAI_API_KEY: Optional[str] = None
    ZAI_BASE_URL: str = "https://open.bigmodel.cn/api/paas/v4"

    # SiliconFlow (Permanent $0 Open Weights Catalog)
    SILICONFLOW_API_KEY: Optional[str] = None
    SILICONFLOW_BASE_URL: str = "https://api.siliconflow.com/v1"

    HF_EMBEDDING_URL: Optional[str] = None  # Self-hosted unlimited HF Space TEI endpoint
    HF_API_KEY: Optional[str] = None

    # Alert notification defaults
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    DEFAULT_ALERT_THRESHOLD: int = 7

    # Memory & System resource limits (<900MB Linux container budget)
    MEMORY_BUDGET_MB: int = 900

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: Optional[str]) -> str:
        if not v:
            return "sqlite+aiosqlite:///./osint.db"
        # Convert standard postgres:// or postgresql:// to postgresql+asyncpg:// if needed
        if v.startswith("postgres://"):
            return v.replace("postgres://", "postgresql+asyncpg://", 1)
        if v.startswith("postgresql://") and not v.startswith("postgresql+asyncpg://"):
            return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v


@lru_cache()
def get_settings() -> Settings:
    """Return cached application settings instance."""
    return Settings()
