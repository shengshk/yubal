"""Cookie conversion utilities for ytmusicapi authentication.

Converts Netscape cookie format (used by yt-dlp) to ytmusicapi auth format.
"""

import hashlib
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Origin for SAPISIDHASH calculation
YTM_ORIGIN = "https://music.youtube.com"

# Essential cookies required for YouTube Music authentication.
# Browser cookie exports often include many unnecessary cookies that can cause
# HTTP 413 (Request Entity Too Large) errors. Only these are needed for auth.
ESSENTIAL_COOKIES = frozenset(
    {
        # Core auth cookies (SAPISID variants)
        "__Secure-3PAPISID",
        "__Secure-1PAPISID",
        "SAPISID",
        "APISID",
        # Session IDs
        "SID",
        "HSID",
        "SSID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        # Session validation
        "SIDCC",
        "__Secure-1PSIDCC",
        "__Secure-3PSIDCC",
        # Session timestamps
        "__Secure-1PSIDTS",
        "__Secure-3PSIDTS",
        # Login info
        "LOGIN_INFO",
    }
)

# Minimum required cookies for authentication to work.
# If any of these are missing, auth will fail with "Sign in" response.
REQUIRED_AUTH_COOKIES = frozenset(
    {
        "SID",
        "HSID",
        "SSID",
    }
)

# Minimum number of essential cookies expected from a valid browser export.
# If fewer than this are found, the cookies may be from a non-authenticated session.
MIN_EXPECTED_COOKIES = 5


def parse_netscape_cookies(cookies_path: Path) -> dict[str, str]:
    """Parse Netscape format cookies.txt into a dict.

    Args:
        cookies_path: Path to cookies.txt file.

    Returns:
        Dict mapping cookie name to value.
    """
    return {name: value for name, value, _expiry in _iter_netscape_cookies(cookies_path)}


def _iter_netscape_cookies(
    cookies_path: Path,
) -> list[tuple[str, str, int]]:
    """Parse Netscape cookies as ``(name, value, expiry_unix)`` rows.

    ``expiry_unix`` is ``0`` for session cookies (no persistent expiry).
    """
    rows: list[tuple[str, str, int]] = []
    try:
        content = cookies_path.read_text()
    except OSError as e:
        logger.warning("Failed to read cookies file: %s", e)
        return rows

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Netscape format: domain, flag, path, secure, expiry, name, value
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        try:
            expiry = int(parts[4])
        except ValueError:
            expiry = 0
        rows.append((parts[5], parts[6], expiry))
    return rows


# Auth cookies whose expiry we surface in status (first non-zero wins for "soonest").
_STATUS_EXPIRY_COOKIES = frozenset(
    {
        "__Secure-3PAPISID",
        "__Secure-1PAPISID",
        "SAPISID",
        "SID",
        "__Secure-1PSID",
        "__Secure-3PSID",
        "LOGIN_INFO",
    }
)

# Warn in the UI when the soonest auth cookie expires within this window.
_EXPIRING_SOON_SECONDS = 7 * 24 * 3600


def inspect_cookies_file(cookies_path: Path) -> dict[str, object]:
    """Summarize cookies.txt health for the settings UI / status API.

    Returns a plain dict suitable for JSON serialization:
      configured, authenticated, auth_complete, expired, expiring_soon,
      expires_at (ISO-8601 UTC or None), days_remaining, status, missing.
    """
    if not cookies_path.exists():
        return {
            "configured": False,
            "authenticated": False,
            "auth_complete": False,
            "expired": False,
            "expiring_soon": False,
            "expires_at": None,
            "days_remaining": None,
            "status": "missing",
            "missing": [],
        }

    rows = _iter_netscape_cookies(cookies_path)
    cookies = {name: value for name, value, _ in rows}
    authenticated = get_sapisid(cookies) is not None
    auth_complete, missing = validate_auth_cookies(cookies)

    now = int(time.time())
    soonest: int | None = None
    expired = False
    for name, _value, expiry in rows:
        if name not in _STATUS_EXPIRY_COOKIES:
            continue
        if expiry <= 0:
            continue  # session cookie — no calendar expiry
        if expiry <= now:
            expired = True
            continue
        if soonest is None or expiry < soonest:
            soonest = expiry

    days_remaining: int | None = None
    expires_at: str | None = None
    expiring_soon = False
    if soonest is not None:
        left = max(0, soonest - now)
        days_remaining = left // 86400
        expiring_soon = left <= _EXPIRING_SOON_SECONDS
        expires_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(soonest))

    if expired or not authenticated:
        status = "expired" if expired else "incomplete"
    elif not auth_complete:
        status = "incomplete"
    elif expiring_soon:
        status = "expiring_soon"
    else:
        status = "ok"

    return {
        "configured": True,
        "authenticated": authenticated,
        "auth_complete": auth_complete,
        "expired": expired,
        "expiring_soon": expiring_soon,
        "expires_at": expires_at,
        "days_remaining": days_remaining,
        "status": status,
        "missing": missing,
    }


def filter_essential_cookies(cookies: dict[str, str]) -> dict[str, str]:
    """Filter cookies to only those essential for YouTube Music authentication.

    Browser cookie exports often include many unnecessary cookies (tracking,
    preferences, etc.) that bloat the Cookie header. Large headers cause
    HTTP 413 errors from YouTube's servers.

    Args:
        cookies: Dict mapping cookie name to value.

    Returns:
        Dict containing only cookies required for authentication.
    """
    return {k: v for k, v in cookies.items() if k in ESSENTIAL_COOKIES}


def validate_auth_cookies(cookies: dict[str, str]) -> tuple[bool, list[str]]:
    """Validate that cookies contain the minimum required for authentication.

    Args:
        cookies: Dict mapping cookie name to value.

    Returns:
        Tuple of (is_valid, list of missing required cookies).
    """
    missing = [name for name in REQUIRED_AUTH_COOKIES if name not in cookies]
    return len(missing) == 0, missing


def build_cookie_header(cookies: dict[str, str]) -> str:
    """Build Cookie header string from cookie dict.

    Args:
        cookies: Dict mapping cookie name to value.

    Returns:
        Cookie header string (name=value; name2=value2; ...).
    """
    return "; ".join(f"{name}={value}" for name, value in cookies.items())


def get_sapisid(cookies: dict[str, str]) -> str | None:
    """Extract SAPISID value from cookies.

    Tries __Secure-3PAPISID first (newer), then SAPISID (older).

    Args:
        cookies: Dict mapping cookie name to value.

    Returns:
        SAPISID value or None if not found.
    """
    return cookies.get("__Secure-3PAPISID") or cookies.get("SAPISID")


def generate_sapisidhash(sapisid: str, origin: str = YTM_ORIGIN) -> str:
    """Generate SAPISIDHASH authorization value.

    Algorithm reverse-engineered from YouTube's auth system.
    See: https://stackoverflow.com/a/32065323/5726546

    Args:
        sapisid: SAPISID cookie value.
        origin: Origin URL for the hash.

    Returns:
        SAPISIDHASH authorization header value.
    """
    timestamp = str(int(time.time()))
    hash_input = f"{timestamp} {sapisid} {origin}"
    # SECURITY NOTE: SHA-1 is required by YouTube's SAPISIDHASH authentication
    # protocol. This is YouTube's legacy auth system, not a choice we can change.
    # The hash is used for request signing, not password storage.
    sha1_hash = hashlib.sha1(hash_input.encode("utf-8")).hexdigest()
    return f"SAPISIDHASH {timestamp}_{sha1_hash}"


def cookies_to_ytmusic_auth(cookies_path: Path) -> dict[str, str] | None:
    """Convert cookies.txt to ytmusicapi authentication headers.

    Args:
        cookies_path: Path to Netscape format cookies.txt file.

    Returns:
        Dict with auth headers for ytmusicapi, or None if auth not possible.
        The dict can be passed directly to YTMusic() constructor.
    """
    if not cookies_path.exists():
        logger.debug("Cookies file not found: %s", cookies_path)
        return None

    cookies = parse_netscape_cookies(cookies_path)
    if not cookies:
        logger.debug("No cookies parsed from file")
        return None

    sapisid = get_sapisid(cookies)
    if not sapisid:
        logger.warning("No SAPISID cookie found - authentication not possible")
        return None

    # Filter to essential cookies only to avoid HTTP 413 errors.
    # Browser exports can include 90KB+ of cookies, but YouTube's servers
    # reject requests with headers that large.
    filtered = filter_essential_cookies(cookies)
    logger.debug(
        "Filtered cookies from %d to %d for ytmusicapi auth",
        len(cookies),
        len(filtered),
    )

    # Validate that required session cookies are present
    is_valid, missing = validate_auth_cookies(filtered)
    if not is_valid:
        logger.warning(
            "Cookies may be incomplete - missing required cookies: %s. "
            "Authentication may fail. Please export cookies while logged into "
            "YouTube Music.",
            ", ".join(missing),
        )

    # Warn if very few essential cookies found (likely non-authenticated export)
    if len(filtered) < MIN_EXPECTED_COOKIES:
        logger.warning(
            "Only %d essential cookies found (expected at least %d). "
            "Cookies may have been exported from a non-authenticated session.",
            len(filtered),
            MIN_EXPECTED_COOKIES,
        )

    cookie_header = build_cookie_header(filtered)
    authorization = generate_sapisidhash(sapisid)

    # Return headers in the format ytmusicapi expects
    return {
        "accept": "*/*",
        "authorization": authorization,
        "content-type": "application/json",
        "x-goog-authuser": "0",
        "x-origin": YTM_ORIGIN,
        "cookie": cookie_header,
    }


def is_authenticated_cookies(cookies_path: Path) -> bool:
    """Check if cookies file contains YouTube Music authentication.

    Args:
        cookies_path: Path to cookies.txt file.

    Returns:
        True if file exists and contains SAPISID cookie.
    """
    if not cookies_path.exists():
        return False

    cookies = parse_netscape_cookies(cookies_path)
    return get_sapisid(cookies) is not None
