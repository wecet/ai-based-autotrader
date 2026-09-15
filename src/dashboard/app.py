"""Authenticated control API with allowlisted configuration and no broker access."""
from datetime import datetime
import hmac
import json
from pathlib import Path
import secrets
import time
from zoneinfo import ZoneInfo

from core.registry import UnknownModel
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from configuration.settings import LiveSettingsStore, SettingsConflict, TradingSettings, atomic_text, json_object, serialize_settings


class SaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    revision: str = Field(pattern=r"^[a-f0-9]{20}$")
    settings: TradingSettings


def access_key(store):
    with store.lock():
        path = store.directory / "dashboard.key"
        if not path.exists():
            atomic_text(path, secrets.token_urlsafe(32) + "\n")
        value = path.read_text().strip()
        if len(value) < 32:
            raise ValueError("Dashboard access key must contain at least 32 characters")
        return value


def status_payload(state_dir, revision):
    def read(name):
        try:
            data = json.loads((state_dir / name).read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    heartbeat = read("heartbeat.json")
    stamp = heartbeat.get("time")
    recent = isinstance(stamp, (float, int)) and 0 <= time.time() - stamp <= 180
    health = "healthy" if recent and heartbeat.get("healthy") is True else "unavailable"
    applied = read("applied-settings.json")
    journal = read("orders.json")
    today = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    attempts = journal.get("attempts")
    count = len(attempts) if isinstance(attempts, list) and journal.get("day") == today else 0
    if (state_dir / "orders.json").exists() and (not isinstance(attempts, list) or not journal.get("day")):
        count = None
    return {
        "health": health,
        "heartbeat_at": stamp if isinstance(stamp, (float, int)) else None,
        "applied_revision": applied.get("revision"),
        "applied_at": applied.get("time"),
        "applied_model_id": applied.get("model_id"),
        "settings_state": "applied" if applied.get("revision") == revision and health == "healthy" else "pending",
        "attempts_today": count,
        "trading_day": today,
    }


def create_app(store: LiveSettingsStore, state_dir: Path, key: str, *, allowed_hosts=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts or ["localhost", "127.0.0.1", "[::1]"])
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.path.startswith("/api/"):
            authorization = request.headers.get("authorization", "")
            if not hmac.compare_digest(authorization.encode(), f"Bearer {key}".encode()):
                return JSONResponse({"detail": "Enter the dashboard access key to unlock settings."}, status_code=401)
            origin = request.headers.get("origin")
            if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
                return JSONResponse({"detail": "Cross-origin requests are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def read_settings():
        try:
            return store.read()
        except (ValueError, OSError):
            raise HTTPException(503, "Settings could not be read. Check the allowlisted configuration and runtime.env.")

    @app.get("/healthz")
    def healthcheck():
        read_settings()
        return {"status": "ok"}

    @app.get("/api/settings")
    def get_settings():
        current = read_settings()
        return {"settings": current.settings.model_dump(), "revision": current.revision,
                "status": status_payload(state_dir, current.revision), **store.registry.catalog()}

    @app.get("/api/models")
    def get_models():
        return store.registry.catalog()

    @app.get("/api/status")
    def get_status():
        current = read_settings()
        return {"revision": current.revision, "status": status_payload(state_dir, current.revision)}

    @app.get("/api/export")
    def export():
        return PlainTextResponse(serialize_settings(read_settings().settings), headers={
            "Content-Disposition": 'attachment; filename="runtime.env"'})

    @app.put("/api/settings")
    async def save_settings(request: Request):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "Settings must be sent as JSON.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 131072:
                raise HTTPException(413, "Settings request is too large.")
        try:
            change = SaveRequest.model_validate(json_object(bytes(body)))
        except ValidationError as error:
            issues = [{"field": ".".join(str(part) for part in item["loc"]), "message": item["msg"]}
                      for item in error.errors(include_input=False, include_context=False, include_url=False)]
            return JSONResponse({"detail": "Please correct the highlighted settings.", "issues": issues}, status_code=422)
        except ValueError:
            raise HTTPException(422, "Use valid JSON without duplicate keys.")
        try:
            saved = store.save(change.settings, change.revision)
        except UnknownModel as error:
            raise HTTPException(422, str(error))
        except SettingsConflict as error:
            raise HTTPException(409, str(error))
        except (ValueError, OSError):
            raise HTTPException(503, "Settings were not saved. Check the runtime configuration volume.")
        return {"settings": saved.settings.model_dump(), "revision": saved.revision,
                "status": status_payload(state_dir, saved.revision)}

    @app.get("/")
    def index():
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
