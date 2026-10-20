"""Runtime configuration, read from environment variables (see docker-compose.yml)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://marketplace:marketplace@localhost:5432/marketplace"
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-secret-change-me")
    access_token_ttl_seconds: int = int(os.getenv("ACCESS_TOKEN_TTL_SECONDS", "900"))  # 15 min
    refresh_token_ttl_seconds: int = int(os.getenv("REFRESH_TOKEN_TTL_SECONDS", str(7 * 24 * 3600)))  # 7 days
    # "N minutes" between CREATE_ORDER / UPDATE_ORDER operations of one user
    order_rate_limit_seconds: int = int(os.getenv("ORDER_RATE_LIMIT_SECONDS", "60"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    port: int = int(os.getenv("PORT", "8000"))


settings = Settings()
