"""Cookies management endpoints.

Handles YouTube Music authentication cookies in Netscape format.
Cookies enable access to private playlists and age-restricted content.
"""

import asyncio

from fastapi import APIRouter
from yubal.utils.cookies import inspect_cookies_file

from yubal_api.api.deps import CookiesFileDep, YtdlpDirDep
from yubal_api.api.exceptions import CookieValidationError
from yubal_api.schemas.cookies import (
    CookiesStatusResponse,
    CookiesUploadRequest,
    CookiesUploadResponse,
)

router = APIRouter(prefix="/cookies", tags=["cookies"])


def _validate_netscape_cookies(content: str) -> None:
    """Validate that content is in Netscape cookie format.

    Raises CookieValidationError if validation fails.
    """
    content = content.strip()
    if not content:
        raise CookieValidationError("Cookie file is empty")

    first_line = content.split("\n")[0]
    # Netscape format: starts with comment (# Netscape...) or domain entry
    if not first_line.startswith(("#", ".")):
        raise CookieValidationError(
            "Invalid cookie format. Expected Netscape format "
            "(file should start with '# Netscape HTTP Cookie File' or a domain entry)"
        )


@router.get("/status")
async def cookies_status(cookies_file: CookiesFileDep) -> CookiesStatusResponse:
    """Check cookies file presence, auth completeness, and calendar expiry."""
    info = await asyncio.to_thread(inspect_cookies_file, cookies_file)
    return CookiesStatusResponse.model_validate(info)


@router.post("")
async def upload_cookies(
    body: CookiesUploadRequest,
    cookies_file: CookiesFileDep,
    ytdlp_dir: YtdlpDirDep,
) -> CookiesUploadResponse:
    """Upload cookies.txt content (Netscape format).

    The cookie file enables downloading from private playlists
    and accessing age-restricted content on YouTube Music.
    """
    _validate_netscape_cookies(body.content)

    await asyncio.to_thread(ytdlp_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread(cookies_file.write_text, body.content)
    return CookiesUploadResponse(status="ok")


@router.delete("")
async def delete_cookies(cookies_file: CookiesFileDep) -> CookiesUploadResponse:
    """Delete the cookies file."""
    if await asyncio.to_thread(cookies_file.exists):
        await asyncio.to_thread(cookies_file.unlink)
    return CookiesUploadResponse(status="ok")
