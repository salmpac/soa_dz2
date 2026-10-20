"""Contract errors: every error leaves the service as {error_code, message, details}."""
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.generated.models import Error, ErrorCode


class ApiError(Exception):
    """Business/contract error. Raised anywhere, rendered by the handler below."""

    def __init__(
        self,
        status_code: int,
        error_code: ErrorCode,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.details = details


def error_response(
    status_code: int, error_code: ErrorCode, message: str, details: Optional[dict[str, Any]] = None
) -> JSONResponse:
    # jsonable_encoder turns Decimal/UUID/datetime inside `details` into JSON-native values
    body = Error(error_code=error_code, message=message, details=jsonable_encoder(details))
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


def _validation_details(exc: RequestValidationError) -> dict[str, Any]:
    errors = []
    for err in exc.errors():
        loc = [str(part) for part in err.get("loc", ())]
        errors.append(
            {
                "field": ".".join(loc) if loc else None,
                "message": err.get("msg"),
                "constraint": err.get("type"),
            }
        )
    return {"errors": errors}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.error_code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # Invalid requests never reach the business logic: FastAPI validates against the
        # generated (contract) models and raises before the handler is called.
        return error_response(
            400, ErrorCode.VALIDATION_ERROR, "Request validation failed", _validation_details(exc)
        )

    @app.exception_handler(ResponseValidationError)
    async def _response_validation_error(_: Request, exc: ResponseValidationError) -> JSONResponse:
        return error_response(500, ErrorCode.INTERNAL_ERROR, "Response does not match the contract")

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return error_response(404, ErrorCode.VALIDATION_ERROR, "Route not found")
        if exc.status_code == 405:
            return error_response(405, ErrorCode.VALIDATION_ERROR, "Method not allowed")
        return error_response(exc.status_code, ErrorCode.INTERNAL_ERROR, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        return error_response(500, ErrorCode.INTERNAL_ERROR, "Internal server error")
