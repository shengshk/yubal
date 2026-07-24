"""Repository for sync ledger entries."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlmodel import Session, col, select

from yubal_api.db.sync_ledger import DIRECT_LEDGER_KEY, LedgerKind, SyncLedgerEntry


class SyncLedgerRepository:
    """CRUD for sync ledger rows."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def list(self) -> list[SyncLedgerEntry]:
        with Session(self._engine) as session:
            stmt = select(SyncLedgerEntry).order_by(
                col(SyncLedgerEntry.updated_at).desc()
            )
            return list(session.exec(stmt).all())

    def mark_stale_running_interrupted(self) -> int:
        """Flip any leftover ``running`` rows to ``interrupted``.

        Jobs live only in memory, so a process restart mid-sync leaves the row
        stuck at ``running`` with no live job to explain it. Called once at
        startup: at that point nothing is actually running, so every
        ``running`` row is by definition a job that was interrupted.
        """
        with Session(self._engine) as session:
            stale = session.exec(
                select(SyncLedgerEntry).where(
                    SyncLedgerEntry.last_job_status == "running"
                )
            ).all()
            for entry in stale:
                entry.last_job_status = "interrupted"
                entry.updated_at = datetime.now(UTC)
                session.add(entry)
            if stale:
                session.commit()
            return len(stale)

    def get_by_key(self, key: str) -> SyncLedgerEntry | None:
        with Session(self._engine) as session:
            stmt = select(SyncLedgerEntry).where(SyncLedgerEntry.key == key)
            return session.exec(stmt).first()

    def upsert(self, entry: SyncLedgerEntry) -> SyncLedgerEntry:
        with Session(self._engine) as session:
            existing = session.exec(
                select(SyncLedgerEntry).where(SyncLedgerEntry.key == entry.key)
            ).first()
            if existing is None:
                session.add(entry)
                session.commit()
                session.refresh(entry)
                return entry

            for field in (
                "kind",
                "subscription_id",
                "save_folder",
                "title",
                "thumbnail_url",
                "content_kind",
                "url",
                "total_count",
                "synced_count",
                "real_download_count",
                "hardlink_count",
                "failed_count",
                "skipped_ugc",
                "skipped_region",
                "skipped_other",
                "last_job_id",
                "last_job_status",
                "last_synced_at",
            ):
                setattr(existing, field, getattr(entry, field))
            existing.updated_at = datetime.now(UTC)
            session.add(existing)
            session.commit()
            session.refresh(existing)
            return existing

    def delete_by_subscription_id(self, subscription_id: UUID) -> bool:
        with Session(self._engine) as session:
            entry = session.exec(
                select(SyncLedgerEntry).where(
                    SyncLedgerEntry.subscription_id == subscription_id
                )
            ).first()
            if entry is None:
                return False
            session.delete(entry)
            session.commit()
            return True

    def delete_by_key(self, key: str) -> bool:
        with Session(self._engine) as session:
            entry = session.exec(
                select(SyncLedgerEntry).where(SyncLedgerEntry.key == key)
            ).first()
            if entry is None:
                return False
            session.delete(entry)
            session.commit()
            return True

    def mark_running(
        self,
        key: str,
        *,
        job_id: str,
        title: str | None = None,
        save_folder: str | None = None,
        subscription_id: UUID | None = None,
        kind: LedgerKind = LedgerKind.SUBSCRIPTION,
        url: str | None = None,
    ) -> SyncLedgerEntry:
        with Session(self._engine) as session:
            entry = session.exec(
                select(SyncLedgerEntry).where(SyncLedgerEntry.key == key)
            ).first()
            if entry is None:
                entry = SyncLedgerEntry(
                    key=key,
                    kind=kind,
                    subscription_id=subscription_id,
                    save_folder=save_folder
                    or ("direct" if key == DIRECT_LEDGER_KEY else "Unknown"),
                    title=title or save_folder or "direct",
                    url=url,
                    last_job_id=job_id,
                    last_job_status="running",
                )
                session.add(entry)
            else:
                entry.last_job_id = job_id
                entry.last_job_status = "running"
                if title:
                    entry.title = title
                if save_folder:
                    entry.save_folder = save_folder
                if url:
                    entry.url = url
                entry.updated_at = datetime.now(UTC)
                session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry
