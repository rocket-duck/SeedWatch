from pydantic import BaseModel, Field, field_validator


class ReleaseCreate(BaseModel):
    url: str = Field(min_length=10, max_length=2000)
    interval_days: int = Field(default=1, ge=1, le=365)
    search_query: str | None = Field(default=None, max_length=500)

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
