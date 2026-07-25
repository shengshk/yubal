"""Custom exceptions and error handlers for the API.

All API errors use a consistent response format:
{
    "error": "error_code",
    "message": "Human-readable description",
    ...additional context fields
}
"""

from typing import Any
from uuid import UUID

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response format."""

    error: str
    message: str


# -- Base Exceptions --


class APIError(Exception):
    """Base exception for API errors.

    Subclasses should define:
    - status_code: HTTP status code
    - error_code: Machine-readable error identifier
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code: str = "internal_error"

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


# -- Job Exceptions --


class JobNotFoundError(APIError):
    """Raised when a job is not found."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "job_not_found"

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Job {job_id} not found")


class JobConflictError(APIError):
    """Raised when a job operation conflicts with existing state."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "job_conflict"

    def __init__(self, message: str, job_id: str | None = None) -> None:
        self.job_id = job_id
        super().__init__(message)


class QueueFullError(APIError):
    """Raised when the job queue is at capacity."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "queue_full"

    def __init__(self) -> None:
        super().__init__("Job queue is full. Wait for existing jobs to complete.")


class DirectDownloadLimitExceededError(APIError):
    """Raised when direct content exceeds the configured track limit."""

    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    error_code = "direct_download_limit_exceeded"

    def __init__(self, *, track_count: int, limit: int) -> None:
        self.track_count = track_count
        self.limit = limit
        super().__init__(
            f"Content contains {track_count} tracks, exceeding the "
            f"instant download limit of {limit}."
        )


# -- Subscription Exceptions --


class SubscriptionNotFoundError(APIError):
    """Raised when a subscription is not found."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "subscription_not_found"

    def __init__(self, subscription_id: UUID) -> None:
        self.subscription_id = subscription_id
        super().__init__(f"Subscription {subscription_id} not found")


class SubscriptionConflictError(APIError):
    """Raised when a subscription operation conflicts with existing state."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "subscription_conflict"

    def __init__(self, message: str, subscription_id: UUID | None = None) -> None:
        self.subscription_id = subscription_id
        super().__init__(message)


class FolderConflictError(APIError):
    """Raised when renaming a save folder would merge into a non-empty target."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "folder_conflict"

    def __init__(
        self,
        message: str,
        *,
        save_folder: str,
        subscription_id: UUID | None = None,
    ) -> None:
        self.save_folder = save_folder
        self.subscription_id = subscription_id
        super().__init__(message)


class MetadataFetchError(APIError):
    """Raised when metadata fetching fails unexpectedly."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "metadata_fetch_failed"

    def __init__(self, message: str, upstream_error: str | None = None) -> None:
        self.upstream_error = upstream_error
        super().__init__(message)


# -- Sync Operation Exceptions --


class DownloadError(APIError):
    """Raised when a download operation fails."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "download_failed"


class CookieValidationError(APIError):
    """Raised when cookie file validation fails."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "invalid_cookies"


class InsufficientDiskSpaceError(APIError):
    """Raised when free space on the data mount is below the configured minimum."""

    status_code = status.HTTP_507_INSUFFICIENT_STORAGE
    error_code = "insufficient_disk_space"

    def __init__(
        self,
        *,
        free_gb: float,
        required_gb: float,
        path: str,
    ) -> None:
        self.free_gb = round(free_gb, 2)
        self.required_gb = required_gb
        self.path = path
        super().__init__(
            f"Not enough free space on {path}: "
            f"{self.free_gb:g} GiB free, need at least {required_gb:g} GiB"
        )


class MigrationInProgressError(APIError):
    """Raised when library migration (or other exclusive maintenance) is running."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "migration_in_progress"

    def __init__(self, reason: str = "library migration") -> None:
        self.reason = reason
        super().__init__(
            f"System is busy ({reason}). Jobs and sync are paused until it finishes."
        )


class MigrationConfirmationRequiredError(APIError):
    """Raised when changing library_layout without confirm_library_migration."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "migration_confirmation_required"

    def __init__(self, *, from_layout: str, to_layout: str) -> None:
        self.from_layout = from_layout
        self.to_layout = to_layout
        super().__init__(
            f"Changing save mode from {from_layout} to {to_layout} reorganizes "
            "the music library on disk. Confirm to proceed; all sync/download "
            "jobs will be stopped for the duration."
        )


class MigrationFailedError(APIError):
    """Raised when layout migration fails; preferences are not updated."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "migration_failed"

    def __init__(self, message: str) -> None:
        super().__init__(message)


# -- External Library Exceptions --


class LibraryUnhealthyError(APIError):
    """Raised when the Download/External mounts are unsafe for library ops."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    error_code = "library_unhealthy"

    def __init__(self, message: str, *, status: str | None = None) -> None:
        self.health_status = status
        super().__init__(message)


class TrackImmutableError(APIError):
    """Raised when an operation would mutate a track sourced read-only."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "track_immutable"

    def __init__(self, message: str = "Track is immutable (read-only source)") -> None:
        super().__init__(message)


# -- Exception Handlers --


def register_exception_handlers(app: FastAPI) -> None:
    """Register custom exception handlers on the FastAPI app."""
    from yubal import (
        AuthenticationRequiredError,
        PlaylistNotFoundError,
        PlaylistParseError,
        TrackNotFoundError,
        UnsupportedPlaylistError,
        UpstreamAPIError,
    )

    # Map yubal core exceptions to HTTP responses
    _CORE_EXCEPTION_MAP: dict[type[Exception], tuple[int, str]] = {
        PlaylistNotFoundError: (404, "playlist_not_found"),
        TrackNotFoundError: (404, "track_not_found"),
        AuthenticationRequiredError: (401, "authentication_required"),
        PlaylistParseError: (422, "playlist_parse_error"),
        UnsupportedPlaylistError: (422, "unsupported_playlist"),
        UpstreamAPIError: (502, "upstream_api_error"),
    }

    for exc_class, (status_code, error_code) in _CORE_EXCEPTION_MAP.items():

        def _make_handler(sc: int, ec: str) -> Any:
            async def handler(request: Request, exc: Exception) -> JSONResponse:
                return JSONResponse(
                    status_code=sc,
                    content={"error": ec, "message": str(exc)},
                )

            return handler

        app.exception_handler(exc_class)(_make_handler(status_code, error_code))

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        """Generic handler for all APIError subclasses."""
        content: dict[str, str | None] = {
            "error": exc.error_code,
            "message": exc.message,
        }

        # Add context fields if present on the exception
        for field in (
            "job_id",
            "subscription_id",
            "upstream_error",
            "save_folder",
            "free_gb",
            "required_gb",
            "path",
            "track_count",
            "limit",
            "reason",
            "from_layout",
            "to_layout",
            "health_status",
        ):
            value = getattr(exc, field, None)
            if value is not None:
                content[field] = str(value)

        return JSONResponse(status_code=exc.status_code, content=content)
