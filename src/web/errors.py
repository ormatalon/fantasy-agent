"""One error shape for the whole API: {"error": {"code", "message", ...extra}} (docs/api-contract.md)."""

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, code: str, status: int, message: str, **extra):
        self.code, self.status, self.message, self.extra = code, status, message, extra


async def api_error_handler(request: Request, e: ApiError) -> JSONResponse:
    return JSONResponse({"error": {"code": e.code, "message": e.message, **e.extra}}, status_code=e.status)


def sleeper_unreachable() -> ApiError:
    return ApiError(
        "sleeper_unreachable", 502, "Could not reach Sleeper. Check your internet connection and try again."
    )
