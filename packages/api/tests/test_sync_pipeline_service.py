from types import SimpleNamespace
from unittest.mock import MagicMock

from yubal_api.services.external_library_service import SyncPlaylistResult
from yubal_api.services.library_enrichment_service import EnrichmentSummary
from yubal_api.services.sync_pipeline_service import SyncPipelineService


def _core() -> tuple[SyncPipelineService, MagicMock, MagicMock]:
    health = MagicMock()
    health.check.return_value = SimpleNamespace(ok=True)
    external = MagicMock()
    external.get_playlist_view.return_value = SimpleNamespace(max_items=50)
    external.sync_playlist.return_value = SyncPlaylistResult(matched=1)
    enrichment = MagicMock()
    enrichment.enrich_library.return_value = EnrichmentSummary(
        enriched=1,
        upgraded=1,
    )
    prefs = MagicMock()
    prefs.effective.return_value = SimpleNamespace(
        external_library_enabled=True,
        wanted_enabled=False,
    )
    service = SyncPipelineService(
        library_health=health,
        external_library_service=external,
        library_enrichment_service=enrichment,
        preferences_store=prefs,
    )
    return service, external, enrichment


def test_playlist_scope_uses_domain_flow_then_verified_enrichment() -> None:
    service, external, enrichment = _core()

    result = service.sync_external_playlist("TEST")

    assert result.matched == 1
    assert result.enriched == 1
    assert result.upgraded == 1
    external.sync_playlist.assert_called_once_with(
        "TEST",
        service._library_health,
        enrich=False,
        raw_match=True,
        verify_meta=True,
        junk_match=False,
    )
    enrichment.enrich_library.assert_called_once_with(
        budget=50,
        reason="playlist:external:TEST",
        save_folder="organized/TEST",
        force=False,
    )
    external.record_playlist_sync_status.assert_called_once_with(
        "TEST",
        status="success",
    )


def test_enabled_global_scope_reuses_the_same_playlist_flow() -> None:
    service, external, _enrichment = _core()
    external.list_playlists.return_value = [
        SimpleNamespace(dir_name="ON", enabled=True, max_items=50),
        SimpleNamespace(dir_name="OFF", enabled=False, max_items=50),
    ]

    result = service.run_external_scan_and_match(trigger="scheduler")

    assert result.matched == 1
    assert external.sync_playlist.call_count == 1
    assert external.sync_playlist.call_args.args[0] == "ON"
