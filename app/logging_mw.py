"""JSON request logging (pure ASGI middleware).

One JSON line per API request with: request_id, method, endpoint, status_code, duration_ms,
user_id, timestamp (+ masked request body for POST/PUT/DELETE).
The request_id is generated here and returned in the X-Request-Id response header.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from starlette.datastructures import MutableHeaders

access_log = logging.getLogger("api.access")

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_KEYS = ("password", "token", "secret", "authorization")
MAX_LOGGED_BODY = 4096


def mask_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: "***" if any(s in k.lower() for s in SENSITIVE_KEYS) else mask_sensitive(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_sensitive(v) for v in value]
    return value


def _loggable_body(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return mask_sensitive(json.loads(raw))
    except ValueError:
        return raw[:MAX_LOGGED_BODY].decode("utf-8", errors="replace")


class RequestLoggingMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = str(uuid.uuid4())
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        method = scope["method"]
        started = time.perf_counter()
        body_chunks: list[bytes] = []
        response: dict[str, int] = {}

        async def receive_wrapper():
            message = await receive()
            if message["type"] == "http.request" and method in MUTATING_METHODS:
                body_chunks.append(message.get("body", b""))
            return message

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                MutableHeaders(scope=message).append("X-Request-Id", request_id)
            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception:
            response.setdefault("status", 500)
            raise
        finally:
            entry = {
                "request_id": request_id,
                "method": method,
                "endpoint": scope["path"],
                "status_code": response.get("status"),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "user_id": state.get("user_id"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if method in MUTATING_METHODS:
                entry["request_body"] = _loggable_body(b"".join(body_chunks))
            access_log.info(entry)


class JsonFormatter(logging.Formatter):
    """Renders every log record (ours and uvicorn's) as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        if isinstance(record.msg, dict):
            payload = {"level": record.levelname, "logger": record.name, **record.msg}
        else:
            payload = {
                "level": record.levelname,
                "logger": record.name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "message": record.getMessage(),
            }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def logging_config(level: str) -> dict:
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {"json": {"()": "app.logging_mw.JsonFormatter"}},
        "handlers": {"stdout": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
        "root": {"level": level, "handlers": ["stdout"]},
        "loggers": {
            "uvicorn": {"level": level, "handlers": ["stdout"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["stdout"], "propagate": False},
            "uvicorn.access": {"level": "WARNING", "handlers": ["stdout"], "propagate": False},
            "api.access": {"level": "INFO", "handlers": ["stdout"], "propagate": False},
        },
    }
