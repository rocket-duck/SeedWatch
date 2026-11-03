from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import ReleaseCreate, ReleaseUpdate
from .monitor import Monitor, MonitorError, timestamp, utc_now
from .prowlarr import ProwlarrError
from .store import Store


settings = Settings.from_env()
store = Store(settings.database_path)
monitor = Monitor(settings, store)
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    await monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="Release Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "prowlarr_configured": bool(settings.prowlarr_api_key)}


@app.get("/api/prowlarr/status")
async def prowlarr_status():
    try:
        return await monitor.prowlarr.status()
    except ProwlarrError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@app.get("/api/releases")
async def list_releases():
    return store.list_releases()


@app.post("/api/releases", status_code=status.HTTP_201_CREATED)
async def add_release(payload: ReleaseCreate):
    try:
        interval_minutes = payload.interval_days * 1440
        return await monitor.add_release(payload.url, interval_minutes, payload.search_query)
    except MonitorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except ProwlarrError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@app.post("/api/releases/{release_id}/check")
async def check_release(release_id: int):
    release = await monitor.check_release(release_id)
    if not release:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздача не найдена")
    return release


@app.patch("/api/releases/{release_id}")
async def update_release(release_id: int, payload: ReleaseUpdate):
    release = store.get_release(release_id)
    if not release:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздача не найдена")
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return release
    if "enabled" in fields:
        fields["next_check_at"] = timestamp() if fields["enabled"] else release["next_check_at"]
    if "interval_days" in fields:
        fields["interval_minutes"] = fields.pop("interval_days") * 1440
        fields["next_check_at"] = timestamp(utc_now() + timedelta(minutes=fields["interval_minutes"]))
    fields["updated_at"] = timestamp()
    return store.update_release(release_id, **fields)


@app.delete("/api/releases/{release_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_release(release_id: int):
    if not store.delete_release(release_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздача не найдена")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/api/releases/{release_id}/history")
async def release_history(release_id: int):
    if not store.get_release(release_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Раздача не найдена")
    return store.list_history(release_id)
