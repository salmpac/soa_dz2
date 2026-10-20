"""FastAPI application assembly.

Routing, request/response models and role checks come from the code generated out of
openapi/openapi.yaml (app/generated, built by scripts/generate.sh). This module only wires
infrastructure: DB pool, error handlers, request logging, and serves the contract itself at /openapi.json.
"""
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI

from app.db import close_pool, open_pool
from app.errors import install_error_handlers
from app.generated.main import api_router
from app.logging_mw import RequestLoggingMiddleware

SPEC_PATH = Path(__file__).resolve().parent.parent / "openapi" / "openapi.yaml"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await open_pool()
    try:
        yield
    finally:
        await close_pool()


app = FastAPI(title="Marketplace API", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
install_error_handlers(app)
app.include_router(api_router)


@app.get("/health", include_in_schema=False)
async def health() -> dict:
    return {"status": "ok"}


# /docs and /openapi.json expose the hand-written contract, not a FastAPI-inferred one.
_spec = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
app.openapi = lambda: _spec  # type: ignore[method-assign]
