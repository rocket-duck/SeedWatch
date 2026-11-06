from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import ConnectionSettings, ReleaseCreate, ReleaseUpdate
from .monitor import Monitor, MonitorError, timestamp, utc_now
from .prowlarr import ProwlarrError
from .qbit import QbitError
from .sonarr import SonarrError
from .store import Store


settings = Settings.from_env()
store = Store(settings.database_path)
monitor = Monitor(settings, store)
static_dir = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    monitor.configure(store.get_settings())
    await monitor.start()
    yield
    await monitor.stop()


app = FastAPI(title="Release Monitor", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.middleware("http")
async def disable_caching(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
async def health():
    return {"status": "ok", "prowlarr_configured": bool(monitor.prowlarr.api_key)}


@app.get("/api/settings")
async def get_connection_settings():
    return monitor.public_settings()


@app.put("/api/settings")
async def update_connection_settings(payload: ConnectionSettings):
    values = payload.model_dump(exclude_none=True)
    for key in list(values):
        values[key] = values[key].strip()
        if key.endswith("_url"):
            if values[key] in ("http://", "https://"):
                values[key] = ""
            elif values[key] and not values[key].lower().startswith(("http://", "https://")):
                values[key] = f"http://{values[key]}"
    for secret in ("prowlarr_api_key", "sonarr_api_key", "qbit_password", "qbit_api_key", "rutracker_password", "kinozal_password"):
        if secret in values and not values[secret]:
            del values[secret]
    store.save_settings(values)
    monitor.configure(store.get_settings())
    return monitor.public_settings()


@app.get("/api/prowlarr/status")
async def prowlarr_status():
    try:
        return await monitor.prowlarr.status()
    except ProwlarrError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc


@app.get("/api/sonarr/status")
async def sonarr_status():
    try:
        return await monitor.sonarr.status()
    except SonarrError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.get("/api/qbit/status")
async def qbit_status():
    try:
        return await monitor.qbit.status()
    except QbitError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.get("/api/sonarr/series")
async def sonarr_series():
    try:
        return await monitor.sonarr.list_series()
    except SonarrError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


@app.get("/api/sonarr/series/{series_id}")
async def sonarr_series_detail(series_id: int):
    try:
        series = await monitor.sonarr.get_series(series_id)
        downloaded = await monitor.sonarr.downloaded_episodes(
            series_id, series.get("seriesType", "anime"), force_absolute=True
        )
    except SonarrError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return {**series, "downloaded_count": len(downloaded)}


@app.get("/api/releases")
async def list_releases():
    return store.list_releases()


@app.post("/api/releases", status_code=status.HTTP_201_CREATED)
async def add_release(payload: ReleaseCreate):
    try:
        interval_minutes = payload.interval_days * 1440
        release = await monitor.add_release(
            payload.url,
            interval_minutes,
            payload.search_query,
            payload.sonarr_series_id,
        )
        return await monitor.check_release(release["id"], initial=True)
    except MonitorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except (ProwlarrError, SonarrError) as exc:
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
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return release
    if "enabled" in fields:
        fields["next_check_at"] = timestamp() if fields["enabled"] else release["next_check_at"]
    if "interval_days" in fields:
        fields["interval_minutes"] = fields.pop("interval_days") * 1440
        fields["next_check_at"] = timestamp(utc_now() + timedelta(minutes=fields["interval_minutes"]))
    if "sonarr_series_id" in fields:
        series_id = fields["sonarr_series_id"]
        if series_id is None:
            fields["sonarr_series_title"] = None
            fields["sonarr_series_type"] = None
        else:
            try:
                series = await monitor.sonarr.get_series(series_id)
            except SonarrError as exc:
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
            fields["sonarr_series_title"] = series["title"]
            fields["sonarr_series_type"] = series.get("seriesType", "standard")
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
