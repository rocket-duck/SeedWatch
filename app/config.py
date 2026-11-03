import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    prowlarr_url: str
    prowlarr_api_key: str
    prowlarr_download_client_id: int | None
    sonarr_url: str
    sonarr_api_key: str
    qbit_url: str
    qbit_auth_method: str
    qbit_username: str
    qbit_password: str
    qbit_api_key: str
    qbit_metadata_timeout: float
    database_path: Path
    request_timeout: float

    @classmethod
    def from_env(cls) -> "Settings":
        download_client_id = os.getenv("PROWLARR_DOWNLOAD_CLIENT_ID", "").strip()
        return cls(
            prowlarr_url=os.getenv("PROWLARR_URL", "http://localhost:9696").rstrip("/"),
            prowlarr_api_key=os.getenv("PROWLARR_API_KEY", "").strip(),
            prowlarr_download_client_id=int(download_client_id) if download_client_id else None,
            sonarr_url=os.getenv("SONARR_URL", "").rstrip("/"),
            sonarr_api_key=os.getenv("SONARR_API_KEY", "").strip(),
            qbit_url=os.getenv("QBIT_URL", "").rstrip("/"),
            qbit_auth_method=os.getenv("QBIT_AUTH_METHOD", "login"),
            qbit_username=os.getenv("QBIT_USERNAME", ""),
            qbit_password=os.getenv("QBIT_PASSWORD", ""),
            qbit_api_key=os.getenv("QBIT_API_KEY", ""),
            qbit_metadata_timeout=float(os.getenv("QBIT_METADATA_TIMEOUT", "60")),
            database_path=Path(os.getenv("DATABASE_PATH", "./data/monitor.db")),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "45")),
        )
