"""Match backoff: linear +24h per fail, reject when delay would exceed cap days."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from yubal_api.db.external_library import (
    MATCH_PENDING,
    MATCH_REJECTED,
    MATCH_UNMATCHED,
    ExternalRawTrack,
)
from yubal_api.services.external_library_service import (
    _BACKOFF_STEP_SECONDS,
    _DEFAULT_BACKOFF_CAP_DAYS,
    ExternalLibraryService,
)


def _svc(*, cap_days: int = 7) -> ExternalLibraryService:
    prefs = MagicMock()
    prefs.effective.return_value = SimpleNamespace(
        match_strictness="strict",
        match_backoff_cap_days=cap_days,
    )
    return ExternalLibraryService(
        MagicMock(), MagicMock(), prefs, ytmusic_client=MagicMock()
    )


def test_backoff_step_is_24h() -> None:
    assert _BACKOFF_STEP_SECONDS == 86400
    assert _DEFAULT_BACKOFF_CAP_DAYS == 7


@pytest.mark.parametrize(
    ("fail_count", "expected_seconds"),
    [
        (0, 0),
        (1, 86400),
        (2, 2 * 86400),
        (7, 7 * 86400),
        (8, 8 * 86400),
    ],
)
def test_backoff_seconds_is_linear_24h(
    fail_count: int, expected_seconds: int
) -> None:
    assert _svc()._backoff_seconds(fail_count) == expected_seconds


@pytest.mark.parametrize(
    ("fail_count_after", "should_reject"),
    [
        (1, False),
        (7, False),  # delay == 7d, not yet rejected
        (8, True),  # delay > 7d → MATCH_REJECTED
        (9, True),
    ],
)
def test_reject_when_delay_exceeds_cap_days(
    fail_count_after: int, should_reject: bool
) -> None:
    assert _svc(cap_days=7)._should_reject_after_fails(fail_count_after) is (
        should_reject
    )


def test_reject_cap_days_is_configurable() -> None:
    # Cap 3 days → reject when fail_count_after > 3
    assert _svc(cap_days=3)._should_reject_after_fails(3) is False
    assert _svc(cap_days=3)._should_reject_after_fails(4) is True


def test_record_failure_sets_linear_next_eligible() -> None:
    repo = MagicMock()
    svc = ExternalLibraryService(
        repo,
        MagicMock(),
        MagicMock(effective=MagicMock(return_value=SimpleNamespace(
            match_backoff_cap_days=7,
            match_strictness="strict",
        ))),
    )
    row = ExternalRawTrack(
        rel_path="plist/a.flac",
        dir_name="plist",
        match_status=MATCH_UNMATCHED,
        match_fail_count=2,
    )
    before = datetime.now(UTC)
    svc._record_failure(row, rejected=False)
    after = datetime.now(UTC)

    repo.record_match_failure.assert_called_once()
    kwargs = repo.record_match_failure.call_args.kwargs
    assert kwargs["rejected"] is False
    next_at: datetime = kwargs["next_eligible_at"]
    # fail_count after increment = 3 → 3 * 24h
    expected = timedelta(seconds=3 * 86400)
    assert before + expected <= next_at <= after + expected


def test_is_junk_rejected_or_readonly_incomplete() -> None:
    rejected = ExternalRawTrack(
        rel_path="a.flac",
        dir_name="d",
        match_status=MATCH_REJECTED,
        title="T",
        artists="A",
        album="Al",
    )
    incomplete = ExternalRawTrack(
        rel_path="b.flac",
        dir_name="d",
        match_status=MATCH_PENDING,
        title="T",
        artists="",
        album="",
    )
    ok = ExternalRawTrack(
        rel_path="c.flac",
        dir_name="d",
        match_status=MATCH_UNMATCHED,
        title="T",
        artists="A",
        album="Al",
    )
    assert ExternalLibraryService.is_junk_row(rejected, readonly=False) is True
    assert ExternalLibraryService.is_junk_row(incomplete, readonly=True) is True
    assert ExternalLibraryService.is_junk_row(incomplete, readonly=False) is False
    assert ExternalLibraryService.is_junk_row(ok, readonly=True) is False
    assert ExternalLibraryService.junk_kind_for_row(rejected, readonly=False) == "rw"
    assert ExternalLibraryService.junk_kind_for_row(rejected, readonly=True) == "ro"
    assert ExternalLibraryService.junk_kind_for_row(incomplete, readonly=True) == "ro"
    assert ExternalLibraryService.junk_kind_for_row(incomplete, readonly=False) is None
    assert ExternalLibraryService.junk_kind_for_row(ok, readonly=True) is None


def test_match_one_rejects_on_eighth_failure() -> None:
    """Eighth consecutive fail (fail_count becomes 8) marks rejected."""
    repo = MagicMock()
    client = MagicMock()
    client.search_songs.return_value = []
    svc = ExternalLibraryService(
        repo,
        MagicMock(),
        MagicMock(
            effective=MagicMock(
                return_value=SimpleNamespace(
                    match_strictness="strict",
                    match_backoff_cap_days=7,
                )
            )
        ),
        ytmusic_client=client,
    )

    row = ExternalRawTrack(
        rel_path="plist/song.flac",
        dir_name="plist",
        match_status=MATCH_PENDING,
        match_fail_count=7,
        title="Song",
        artists="Artist",
        album="Album",
    )
    ok = svc.match_one(row)
    assert ok is False
    repo.record_match_failure.assert_called_once()
    assert repo.record_match_failure.call_args.kwargs["rejected"] is True


def test_seventh_failure_still_pending_not_rejected() -> None:
    repo = MagicMock()
    client = MagicMock()
    client.search_songs.return_value = []
    svc = ExternalLibraryService(
        repo,
        MagicMock(),
        MagicMock(
            effective=MagicMock(
                return_value=SimpleNamespace(
                    match_strictness="strict",
                    match_backoff_cap_days=7,
                )
            )
        ),
        ytmusic_client=client,
    )

    row = ExternalRawTrack(
        rel_path="plist/song.flac",
        dir_name="plist",
        match_status=MATCH_PENDING,
        match_fail_count=6,
        title="Song",
        artists="Artist",
        album="Album",
    )
    ok = svc.match_one(row)
    assert ok is False
    assert repo.record_match_failure.call_args.kwargs["rejected"] is False
    next_at = repo.record_match_failure.call_args.kwargs["next_eligible_at"]
    # fail_count after = 7 → 7 days
    delta = next_at - datetime.now(UTC)
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, minutes=1)
