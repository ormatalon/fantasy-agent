"""FastAPI app: the /api routes and the static page at /. Bind it to 127.0.0.1 only."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import panel, setup
from .errors import ApiError, api_error_handler
from storage import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect()
    db.migrate(conn)
    conn.close()
    yield


app = FastAPI(title="Fantasy Agent", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])
app.add_exception_handler(ApiError, api_error_handler)
app.include_router(setup.router)
app.include_router(panel.router)


@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0"}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
