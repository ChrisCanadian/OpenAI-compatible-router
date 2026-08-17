from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr


class RouteRegistration(BaseModel):
    base_url: str
    api_key: SecretStr
    model: str = Field(min_length=1, max_length=200)
    ttl_seconds: int = Field(default=900, ge=60, le=3600)
    supports_tools: bool = True
    max_completion_tokens: int = Field(default=8192, ge=1, le=65536)


class RouteCreated(BaseModel):
    route_token: str
    expires_at: str
