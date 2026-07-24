"""Local-library presence checks for unified Enter quick-actions (operation B)."""

from __future__ import annotations

from yubal.exceptions import PlaylistParseError
from yubal.utils.library import EXTERNAL_ORGANIZED_DIR
from yubal.utils.url import parse_playlist_id

from yubal_api.db.sync_ledger import DIRECT_LEDGER_KEY
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.schemas.library_lookup import (
    LibraryLocationHit,
    PlaylistPresenceResponse,
    TextMatchHit,
    TextPresenceResponse,
    TrackPresenceResponse,
)
from yubal_api.services.preferences import PreferencesStore
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.sync_ledger_service import (
    SyncLedgerService,
    subscription_ledger_key,
)


def _safe_playlist_id(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return parse_playlist_id(url)
    except PlaylistParseError:
        return None


class LibraryLookupService:
    """Resolve whether a track / playlist / text query already exists locally."""

    def __init__(
        self,
        *,
        catalog: TrackCatalogRepository,
        subscriptions: SubscriptionService,
        preferences: PreferencesStore,
        sync_ledger: SyncLedgerService,
    ) -> None:
        self._catalog = catalog
        self._subscriptions = subscriptions
        self._preferences = preferences
        self._sync_ledger = sync_ledger

    def lookup_track(self, video_id: str) -> TrackPresenceResponse:
        vid = (video_id or "").strip()
        if not vid:
            return TrackPresenceResponse(video_id="")

        record = self._catalog.get_track(vid)
        locations = self._catalog.list_locations_for_video(vid)
        direct_folder = self._preferences.effective().direct_folder
        in_direct = any(
            loc.save_folder.strip().replace("\\", "/").rstrip("/") == direct_folder
            for loc in locations
        )
        hits = self._locations_to_hits(
            {loc.save_folder for loc in locations},
            exclude_direct=direct_folder,
        )
        return TrackPresenceResponse(
            video_id=vid,
            title=record.title if record else None,
            artist=record.artist if record else None,
            in_direct=in_direct,
            locations=hits,
        )

    def lookup_playlist(self, url: str) -> PlaylistPresenceResponse:
        target = (url or "").strip()
        sub_hit = self._find_subscription_hit(target)
        in_direct_url = self._direct_url_matches(target)
        return PlaylistPresenceResponse(
            url=target,
            subscription=sub_hit,
            in_direct_url=in_direct_url,
        )

    def lookup_text(self, query: str, *, limit: int = 20) -> TextPresenceResponse:
        q = (query or "").strip()
        if not q:
            return TextPresenceResponse(query="")
        matches: list[TextMatchHit] = []
        direct_folder = self._preferences.effective().direct_folder
        for record in self._catalog.search_tracks(q, limit=limit):
            locations = self._catalog.list_locations_for_video(record.video_id)
            in_direct = any(
                loc.save_folder.strip().replace("\\", "/").rstrip("/")
                == direct_folder
                for loc in locations
            )
            hits = self._locations_to_hits(
                {loc.save_folder for loc in locations},
                exclude_direct=direct_folder,
            )
            matches.append(
                TextMatchHit(
                    video_id=record.video_id,
                    title=record.title,
                    artist=record.artist,
                    in_direct=in_direct,
                    locations=hits,
                )
            )
        return TextPresenceResponse(query=q, matches=matches)

    def _direct_url_matches(self, url: str) -> bool:
        entry = next(
            (
                e
                for e in self._sync_ledger.list(reconcile=False)
                if e.key == DIRECT_LEDGER_KEY
            ),
            None,
        )
        if entry is None or not entry.url:
            return False
        if entry.url.strip() == url.strip():
            return True
        left = _safe_playlist_id(entry.url)
        right = _safe_playlist_id(url)
        return bool(left and right and left == right)

    def _find_subscription_hit(self, url: str) -> LibraryLocationHit | None:
        target_pid = _safe_playlist_id(url)
        for sub in self._subscriptions.list(enabled=None):
            if sub.url.strip() == url.strip():
                return LibraryLocationHit(
                    kind="subscription",
                    expand_key=subscription_ledger_key(sub.id),
                    title=sub.name,
                    enabled=sub.enabled,
                )
            if target_pid and _safe_playlist_id(sub.url) == target_pid:
                return LibraryLocationHit(
                    kind="subscription",
                    expand_key=subscription_ledger_key(sub.id),
                    title=sub.name,
                    enabled=sub.enabled,
                )
        return None

    def _locations_to_hits(
        self,
        save_folders: set[str],
        *,
        exclude_direct: str,
    ) -> list[LibraryLocationHit]:
        """Map catalog folders → expandable playlist hits.

        Order: subscriptions (UI list order) then external (dir_name order).
        """
        normalized = {
            f.strip().replace("\\", "/").rstrip("/")
            for f in save_folders
            if f and f.strip()
        }
        normalized.discard(exclude_direct)

        subs = self._subscriptions.list(enabled=None)
        hits: list[LibraryLocationHit] = []
        seen: set[str] = set()

        for sub in subs:
            folder = (sub.save_folder or sub.name or "").strip().replace("\\", "/")
            folder = folder.rstrip("/")
            if folder not in normalized:
                continue
            key = subscription_ledger_key(sub.id)
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                LibraryLocationHit(
                    kind="subscription",
                    expand_key=key,
                    title=sub.name,
                    enabled=sub.enabled,
                )
            )

        prefix = f"{EXTERNAL_ORGANIZED_DIR}/"
        external_dirs: list[str] = []
        for folder in normalized:
            if not folder.startswith(prefix):
                continue
            dir_name = folder[len(prefix) :].split("/", 1)[0]
            if dir_name:
                external_dirs.append(dir_name)

        for dir_name in sorted(set(external_dirs)):
            key = f"external:{dir_name}"
            if key in seen:
                continue
            seen.add(key)
            hits.append(
                LibraryLocationHit(
                    kind="external",
                    expand_key=key,
                    title=dir_name,
                    enabled=None,
                )
            )

        return hits
