"""`python -m app` — runs the API with JSON logging."""
import uvicorn

from app.config import settings
from app.logging_mw import logging_config

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.port,
        log_config=logging_config(settings.log_level),
        access_log=False,
    )
