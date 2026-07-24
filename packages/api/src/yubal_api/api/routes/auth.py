"""Auth API routes and middleware."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response as StarletteResponse

from yubal_api.services.auth import COOKIE_NAME, AuthManager

router = APIRouter(tags=["auth"])


class LoginBody(BaseModel):
    username: str = ""
    password: str = ""
    remember: bool = False


class SetupBody(BaseModel):
    username: str = ""
    password: str = ""
    confirm_password: str = Field(default="", alias="confirmPassword")

    model_config = {"populate_by_name": True}


def get_auth(request: Request) -> AuthManager:
    return request.app.state.auth  # type: ignore[no-any-return]


def _is_https(request: Request) -> bool:
    return request.url.scheme == "https" or (
        request.headers.get("x-forwarded-proto", "").lower() == "https"
    )


def _set_session_cookie(
    response: Response,
    request: Request,
    value: str,
    max_age: int,
) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=value,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=_is_https(request),
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="strict",
        secure=_is_https(request),
    )


def _http_error(message: str) -> HTTPException:
    code = 401
    if message == "auth setup required":
        code = 409
    elif message == "auth setup expired":
        code = 423
    elif message == "auth already initialized":
        code = 409
    elif message in {
        "username is required",
        "username is too long",
        "password is required",
        "passwords do not match",
    }:
        code = 400
    return HTTPException(status_code=code, detail=message)


@router.get("/auth")
def auth_status(
    request: Request,
    yubal_session: Annotated[str | None, Cookie(alias=COOKIE_NAME)] = None,
) -> dict:
    return get_auth(request).status(yubal_session).to_dict()


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response) -> dict:
    auth = get_auth(request)
    ok, err, cookie = auth.login(body.username, body.password, body.remember)
    if not ok:
        raise _http_error(err)
    if cookie:
        _set_session_cookie(
            response, request, cookie, auth.cookie_max_age(body.remember)
        )
    status = auth.status(cookie)
    if not auth.enabled:
        status.authenticated = True
    elif cookie:
        status.authenticated = True
        status.username = body.username.strip()
    return status.to_dict()


@router.post("/auth/setup")
def setup(body: SetupBody, request: Request, response: Response) -> dict:
    auth = get_auth(request)
    ok, err, cookie = auth.setup(
        body.username, body.password, body.confirm_password
    )
    if not ok:
        raise _http_error(err)
    if cookie:
        _set_session_cookie(response, request, cookie, auth.cookie_max_age(False))
    status = auth.status(cookie)
    status.authenticated = True
    status.username = body.username.strip()
    status.needs_setup = False
    status.setup_locked = False
    return status.to_dict()


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    auth = get_auth(request)
    _clear_session_cookie(response, request)
    status = auth.status(None)
    status.authenticated = not auth.enabled
    status.username = ""
    return status.to_dict()


PUBLIC_API_PATHS = frozenset(
    {
        "/api/auth",
        "/api/login",
        "/api/logout",
        "/api/auth/setup",
        "/api/health",
    }
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Protect /api/* when built-in auth is enabled."""

    def __init__(self, app, auth: AuthManager, base_path: str = "") -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.auth = auth
        self.base_path = base_path.rstrip("/")

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        if not self.auth.enabled:
            return await call_next(request)

        path = request.url.path
        if self.base_path and path.startswith(self.base_path):
            path = path[len(self.base_path) :] or "/"

        if not path.startswith("/api"):
            return await call_next(request)

        normalized = path.rstrip("/") or "/"
        if normalized in PUBLIC_API_PATHS:
            return await call_next(request)

        cookie = request.cookies.get(COOKIE_NAME)
        if self.auth.needs_setup():
            return JSONResponse(
                {
                    "error": "auth setup required",
                    "message": "auth setup required",
                },
                status_code=401,
            )
        if not self.auth.valid_session(cookie):
            return JSONResponse(
                {
                    "error": "authentication required",
                    "message": "authentication required",
                },
                status_code=401,
            )
        return await call_next(request)
