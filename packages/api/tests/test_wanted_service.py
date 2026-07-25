from types import SimpleNamespace
from unittest.mock import MagicMock

from yubal_api.services.wanted_service import (
    WantedEnrichmentSummary,
    WantedService,
)


class _FakeClient:
    def search_songs(self, _query: str) -> list[SimpleNamespace]:
        return [
            SimpleNamespace(
                title="我的楼兰",
                artists=[SimpleNamespace(name="云朵")],
                video_id="matched-video-id",
            )
        ]


def test_search_ytm_video_id_uses_numeric_match_scores() -> None:
    service = WantedService.__new__(WantedService)
    service._client = _FakeClient()
    row = SimpleNamespace(title="我的楼兰", artists="云朵", album="倔强")

    assert service._search_ytm_video_id(row) == "matched-video-id"


def test_sync_pass_enriches_existing_files_before_ytm_fulfill() -> None:
    service = WantedService.__new__(WantedService)
    order: list[str] = []
    service._preferences = MagicMock()
    service._preferences.effective.return_value = SimpleNamespace(wanted_max_items=50)
    service.match_local_batch = MagicMock(
        side_effect=lambda **_kwargs: order.append("link") or 1
    )
    service.enrich_existing_files = MagicMock(
        side_effect=lambda **_kwargs: (
            order.append("enrich")
            or WantedEnrichmentSummary(
                scanned=1,
                completed=1,
                lyrics_written=1,
            )
        )
    )
    service.match_ytm_batch = MagicMock(
        side_effect=lambda **_kwargs: order.append("ytm") or {"matched": 1, "failed": 0}
    )
    service.record_sync_result = MagicMock()

    result = service.run_sync_pass()

    assert result["linked"] == 1
    assert result["lyrics_written"] == 1
    assert result["matched"] == 1
    assert order == ["link", "enrich", "ytm"]
    service.match_ytm_batch.assert_called_once_with(limit=25, force=False)
