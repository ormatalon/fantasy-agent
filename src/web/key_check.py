"""Check an OpenRouter key before it is stored. The key is only sent to OpenRouter."""

import os
import sqlite3

import httpx

from storage.db import get_setting
from .errors import ApiError

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def base_url(conn: sqlite3.Connection) -> str:
    """Setting first, then OPENROUTER_BASE_URL, then OpenRouter. Tests point it at a stub."""
    return get_setting(conn, "openrouter_base_url") or os.environ.get("OPENROUTER_BASE_URL") or DEFAULT_BASE_URL


def verify(key: str, base: str) -> None:
    """Return when OpenRouter accepts the key; raise key_rejected or provider_error."""
    try:
        r = httpx.get(f"{base.rstrip('/')}/key", headers={"Authorization": f"Bearer {key}"}, timeout=15)
    except httpx.HTTPError:
        raise ApiError("provider_error", 502, "Could not reach OpenRouter to check the key. Try again.") from None
    if r.status_code == 401:
        raise ApiError(
            "key_rejected",
            422,
            "OpenRouter did not accept this key. Check that you copied all of it, or create a new one.",
        )
    if r.status_code != 200:
        raise ApiError("provider_error", 502, "OpenRouter could not check the key. Try again.")
