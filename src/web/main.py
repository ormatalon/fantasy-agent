"""FastAPI app: the /api routes and the static page at /. Bind it to 127.0.0.1 only."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import panel, setup
from .errors import ApiError, api_error_handler
from ingestion.nfl import service as nfl
from storage import db


def poll_nfl() -> None:
    """One pass over the NFL sources that are due (section 5). Failures are stored in refresh_log."""
    conn = db.connect()
    try:
        nfl.refresh(conn)
    except Exception:
        logging.getLogger(__name__).exception("NFL refresh failed")
    finally:
        conn.close()


async def poll_loop() -> None:
    while True:
        await asyncio.to_thread(poll_nfl)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    task = asyncio.create_task(poll_loop()) if os.environ.get("FANTASY_POLL", "1") == "1" else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="Fantasy Agent", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.add_exception_handler(ApiError, api_error_handler)
app.include_router(setup.router)
app.include_router(panel.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
