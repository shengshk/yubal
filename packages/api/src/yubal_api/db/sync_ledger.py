"""Persistent sync ledger — one row per subscription save folder + Direct."""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlmodel import Field, SQLModel


DIRECT_LEDGER_KEY = "direct"


class LedgerKind(StrEnum):
    """Ledger entry kind."""

    SUBSCRIPTION = "subscription"
    DIRECT = "direct"


class SyncLedgerEntry(SQLModel, table=True):
    """Durable sync facts for the Sync Center UI."""

    __tablename__ = "sync_ledger"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    # Unique business key: "direct" or "subscription:<uuid>"
    key: str = Field(unique=True, index=True, max_length=80)
    kind: LedgerKind = Field(index=True)
    subscription_id: UUID | None = Field(default=None, index=True)
    save_folder: str = Field(max_length=200)
    title: str = Field(max_length=200)
    thumbnail_url: str | None = Field(default=None, max_length=2048)
    content_kind: str = Field(default="playlist", max_length=32)
    url: str | None = Field(default=None, max_length=2048)

    total_count: int = Field(default=0, ge=0)
    synced_count: int = Field(default=0, ge=0)
    real_download_count: int = Field(default=0, ge=0)
    hardlink_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    skipped_ugc: int = Field(default=0, ge=0)
    skipped_region: int = Field(default=0, ge=0)
    skipped_other: int = Field(default=0, ge=0)

    last_job_id: str | None = Field(default=None, max_length=64)
    last_job_status: str | None = Field(default=None, max_length=32)
    last_synced_at: datetime | None = Field(default=None)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
