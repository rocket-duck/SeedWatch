from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ReleaseCreate(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    interval_days: int = Field(default=1, ge=1, le=365)
    search_query: str | None = Field(default=None, max_length=500)
    sonarr_series_id: int | None = Field(default=None, gt=0)

    @field_validator("url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("search_query")
    @classmethod
    def strip_query(cls, value: str | None) -> str | None:
        value = value.strip() if value else ""
        return value or None


class ReleaseUpdate(BaseModel):
    enabled: bool | None = None
    interval_days: int | None = Field(default=None, ge=1, le=365)


class ConnectionSettings(BaseModel):
    prowlarr_url: str | None = None
    prowlarr_api_key: str | None = None
    sonarr_url: str | None = None
    sonarr_api_key: str | None = None
    qbit_url: str | None = None
    qbit_auth_method: Literal["login", "api_key"] | None = None
    qbit_username: str | None = None
    qbit_password: str | None = None
    qbit_api_key: str | None = None
