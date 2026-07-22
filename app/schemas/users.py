from pydantic import BaseModel, Field


class SettingsModel(BaseModel):
    quality: str = "MP3_320"
    make_zip: bool = True
    language: str = "fa"


class ResolveRequest(BaseModel):
    platform_user_id: str = Field(..., max_length=64)
    display_name: str | None = Field(default=None, max_length=255)


class ResolveResponse(BaseModel):
    user_id: int
    is_new: bool
    settings: SettingsModel


class SettingsUpdate(BaseModel):
    quality: str | None = None
    make_zip: bool | None = None
    language: str | None = None


class LinkCodeResponse(BaseModel):
    code: str
    expires_at: str  # ISO-8601 UTC
    expires_in_seconds: int


class LinkRequest(BaseModel):
    code: str = Field(..., max_length=16)
    platform_user_id: str = Field(..., max_length=64)  # e.g. Android device/install id
    display_name: str | None = Field(default=None, max_length=255)
