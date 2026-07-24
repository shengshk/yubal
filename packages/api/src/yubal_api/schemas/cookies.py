"""Cookies schemas."""

from typing import Literal

from pydantic import BaseModel, Field


class CookiesStatusResponse(BaseModel):
    """Cookies status response model."""

    configured: bool
    authenticated: bool = False
    auth_complete: bool = False
    expired: bool = False
    expiring_soon: bool = False
    expires_at: str | None = None
    days_remaining: int | None = None
    status: Literal[
        "missing", "ok", "expired", "incomplete", "expiring_soon"
    ] = "missing"
    missing: list[str] = Field(default_factory=list)


class CookiesUploadRequest(BaseModel):
    """Cookies upload request model."""

    content: str


class CookiesUploadResponse(BaseModel):
    """Cookies upload response model."""

    status: Literal["ok"]
