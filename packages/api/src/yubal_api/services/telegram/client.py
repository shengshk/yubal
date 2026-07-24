"""Thin Telegram Bot API client (official or local tgapi)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)
# Never let httpx INFO print Bot API URLs (they embed the token).
logging.getLogger("httpx").setLevel(logging.WARNING)

OFFICIAL_API = "https://api.telegram.org"
# Match /bot<token>/ in URLs so logs never leak the secret.
_TOKEN_IN_URL = re.compile(r"/bot[^/\s]+")
_AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
}


class BotApiClient:
    """Minimal async wrapper around Telegram Bot HTTP API."""

    def __init__(self, token: str, *, api_base: str = "") -> None:
        self._token = token.strip()
        base = (api_base or OFFICIAL_API).rstrip("/")
        self._base = f"{base}/bot{self._token}"
        self._local = bool(api_base.strip())
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0))

    @property
    def is_local(self) -> bool:
        return self._local

    def _redact(self, text: str) -> str:
        out = text or ""
        if self._token:
            out = out.replace(self._token, "<TOKEN>")
        return _TOKEN_IN_URL.sub("/bot<TOKEN>", out)

    async def close(self) -> None:
        await self._client.aclose()

    async def call(
        self,
        method: str,
        *,
        form: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        **params: Any,
    ) -> dict[str, Any]:
        url = f"{self._base}/{method}"
        try:
            if files is not None or form is not None:
                data = dict(form or {})
                data.update({k: v for k, v in params.items() if v is not None})
                for key, value in list(data.items()):
                    if isinstance(value, (dict, list)):
                        data[key] = json.dumps(value, ensure_ascii=False)
                response = await self._client.post(url, data=data, files=files)
            else:
                response = await self._client.post(url, json=params)
            if response.status_code >= 400:
                body = (response.text or "")[:400]
                logger.warning(
                    "Telegram API %s failed: HTTP %s %s",
                    method,
                    response.status_code,
                    self._redact(body),
                )
                response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError as exc:
            logger.warning(
                "Telegram API %s failed: %s", method, self._redact(str(exc))
            )
            raise
        if not payload.get("ok"):
            desc = payload.get("description") or "unknown error"
            raise RuntimeError(f"Telegram {method}: {desc}")
        return payload.get("result") or {}

    async def get_updates(
        self, *, offset: int | None = None, timeout: int = 25
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            params["offset"] = offset
        result = await self.call("getUpdates", **params)
        return result if isinstance(result, list) else []

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        disable_notification: bool = False,
        parse_mode: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": disable_notification,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        if parse_mode:
            params["parse_mode"] = parse_mode
        return await self.call("sendMessage", **params)

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        try:
            await self.call("deleteMessage", chat_id=chat_id, message_id=message_id)
        except Exception as exc:
            logger.debug(
                "deleteMessage %s/%s failed: %s",
                chat_id,
                message_id,
                self._redact(str(exc)),
            )

    async def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
        }
        if reply_markup is not None:
            params["reply_markup"] = reply_markup
        return await self.call("editMessageText", **params)

    async def answer_callback(
        self,
        callback_id: str,
        *,
        text: str | None = None,
        show_alert: bool = False,
    ) -> None:
        """Ack a callback immediately. Never raise — TG expires queries in ~seconds."""
        if not callback_id:
            return
        params: dict[str, Any] = {
            "callback_query_id": callback_id,
            "show_alert": show_alert,
        }
        if text:
            params["text"] = text[:200]
        try:
            await self.call("answerCallbackQuery", **params)
        except Exception as exc:
            logger.debug(
                "answerCallbackQuery ignored: %s", self._redact(str(exc))
            )

    async def send_audio_by_file_id(
        self,
        chat_id: int,
        file_id: str,
        *,
        title: str | None = None,
        performer: str | None = None,
        duration: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"chat_id": chat_id, "audio": file_id}
        if title:
            params["title"] = title
        if performer:
            params["performer"] = performer
        if duration is not None and duration > 0:
            params["duration"] = int(duration)
        return await self.call("sendAudio", **params)

    async def send_audio_path(
        self,
        chat_id: int,
        path: Path,
        *,
        filename: str,
        title: str | None = None,
        performer: str | None = None,
        duration: int | None = None,
    ) -> dict[str, Any]:
        """sendAudio with multipart filename + title/performer + MIME."""
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Audio file missing: {resolved}")
        form: dict[str, Any] = {"chat_id": str(chat_id)}
        if title:
            form["title"] = title
        if performer:
            form["performer"] = performer
        if duration is not None and duration > 0:
            form["duration"] = str(int(duration))
        safe_name = filename.strip() or resolved.name
        mime = _AUDIO_MIME.get(resolved.suffix.lower(), "application/octet-stream")
        with resolved.open("rb") as handle:
            files = {
                "audio": (safe_name, handle, mime),
            }
            return await self.call("sendAudio", form=form, files=files)

    async def send_document_by_file_id(
        self,
        chat_id: int,
        file_id: str,
    ) -> dict[str, Any]:
        return await self.call(
            "sendDocument", chat_id=chat_id, document=file_id
        )

    async def send_document_path(
        self,
        chat_id: int,
        path: Path,
        *,
        filename: str,
    ) -> dict[str, Any]:
        """Send original bytes as a document (keeps extension)."""
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Audio file missing: {resolved}")

        form: dict[str, Any] = {"chat_id": str(chat_id)}
        safe_name = filename.strip() or resolved.name

        with resolved.open("rb") as handle:
            files = {
                "document": (safe_name, handle, "application/octet-stream"),
            }
            return await self.call("sendDocument", form=form, files=files)

    async def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        await self.call("setMyCommands", commands=commands)

    async def delete_my_commands(self) -> None:
        await self.call("deleteMyCommands")
