import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    prowlarr_url: str
    prowlarr_api_key: str
    prowlarr_download_client_id: int | None
    database_path: Path
    request_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        download_client_id = os.getenv("PROWLARR_DOWNLOAD_CLIENT_ID", "").strip()
        return cls(
            prowlarr_url=os.getenv("PROWLARR_URL", "http://localhost:9696").rstrip("/"),
            prowlarr_api_key=os.getenv("PROWLARR_API_KEY", "").strip(),
            prowlarr_download_client_id=int(download_client_id) if download_client_id else None,
            database_path=Path(os.getenv("DATABASE_PATH", "./data/monitor.db")),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "45")),
        )
