"""FastAPI application factory and configuration."""

import asyncio
import logging
import mimetypes
import re
import shutil
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from pydantic import TypeAdapter
from rich.console import Console
from rich.logging import RichHandler
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import HTMLResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope
from yubal import cleanup_part_files
from yubal.services.track_index import repair_track_index
from yubal.utils.cleanup import cleanup_startup_temps
from yubal.utils.library import (
    DOWNLOAD_ROOT,
    EXTERNAL_ROOT,
    WANTED_ROOT,
    ensure_external_layout,
    ensure_wanted_layout,
)

from yubal_api.api.container import Services
from yubal_api.api.exceptions import register_exception_handlers
from yubal_api.api.routes import (
    auth,
    cookies,
    external,
    health,
    info,
    jobs,
    library,
    logs,
    scheduler,
    subscriptions,
    sync_ledger,
)
from yubal_api.api.routes import (
    search as search_routes,
)
from yubal_api.api.routes import (
    settings as settings_routes,
)
from yubal_api.api.routes import (
    wanted as wanted_routes,
)
from yubal_api.api.routes.auth import AuthMiddleware
from yubal_api.db import SubscriptionRepository, create_db_engine
from yubal_api.db.external_library_repository import ExternalLibraryRepository
from yubal_api.db.preselect_repository import PreselectRepository
from yubal_api.db.subscription_membership_repository import (
    SubscriptionMembershipRepository,
    SubscriptionSnapshotRepository,
)
from yubal_api.db.sync_ledger_repository import SyncLedgerRepository
from yubal_api.db.track_catalog_repository import TrackCatalogRepository
from yubal_api.db.wanted_repository import WantedRepository
from yubal_api.schemas.jobs import (
    ClearedEvent,
    CreatedEvent,
    DeletedEvent,
    SnapshotEvent,
    UpdatedEvent,
)
from yubal_api.schemas.logs import LogEntry
from yubal_api.services.auth import AuthManager
from yubal_api.services.catalog_folder_presence import CatalogFolderPresence
from yubal_api.services.database_safety import (
    create_verified_database_backup,
    verify_sqlite_database,
)
from yubal_api.services.external_library_service import ExternalLibraryService
from yubal_api.services.job_event_bus import JobEventBus
from yubal_api.services.job_executor import JobExecutor
from yubal_api.services.job_store import JobStore
from yubal_api.services.library_dedup_service import LibraryDedupService
from yubal_api.services.library_enrichment_service import LibraryEnrichmentService
from yubal_api.services.library_health_service import LibraryHealthService
from yubal_api.services.library_lookup_service import LibraryLookupService
from yubal_api.services.library_stats_service import LibraryStatsService
from yubal_api.services.log_buffer import BufferHandler, LogBuffer
from yubal_api.services.operation_gate import OperationGate
from yubal_api.services.playlist_info_service import PlaylistInfoService
from yubal_api.services.preferences import (
    DOWNLOAD_CACHE_ROOT,
    PreferencesStore,
    preferences_from_settings,
)
from yubal_api.services.preselect_service import PreselectService
from yubal_api.services.scheduler import Scheduler
from yubal_api.services.search_service import SearchService
from yubal_api.services.shutdown_coordinator import ShutdownCoordinator
from yubal_api.services.subscription_membership_service import (
    SubscriptionMembershipService,
)
from yubal_api.services.subscription_service import SubscriptionService
from yubal_api.services.sync_ledger_service import SyncLedgerService
from yubal_api.services.sync_pipeline_service import SyncPipelineService
from yubal_api.services.telegram import TelegramBotService
from yubal_api.services.track_metadata_service import TrackMetadataService
from yubal_api.services.track_retag_service import TrackRetagService
from yubal_api.services.wanted_service import WantedService
from yubal_api.services.wash_service import WashService
from yubal_api.settings import get_settings

# Global reference for shutdown suppression
_rich_console: Console | None = None


def setup_logging() -> None:
    """Configure logging with Rich handler for all loggers including uvicorn."""
    global _rich_console

    settings = get_settings()
    console = Console(force_terminal=True)
    _rich_console = console  # Store for shutdown suppression

    handler = RichHandler(
        console=console, rich_tracebacks=True, show_path=False, markup=True
    )
    handler.setFormatter(logging.Formatter("%(name)s - %(message)s", datefmt="[%X]"))

    # Configure root logger
    logging.root.handlers = [handler]
    logging.root.setLevel(settings.log_level)

    # Configure uvicorn loggers to use Rich
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = [handler]
        uvicorn_logger.propagate = False


def setup_log_streaming(log_buffer: LogBuffer) -> None:
    """Attach buffer handler to capture logs for SSE streaming."""
    buffer_handler = BufferHandler(log_buffer)
    buffer_handler.setLevel(logging.INFO)
    logging.getLogger("yubal").addHandler(buffer_handler)
    logging.getLogger("yubal_api").addHandler(buffer_handler)


def suppress_logging() -> None:
    """Suppress most logging output during shutdown.

    Keeps ERROR level visible but suppresses INFO/WARNING to prevent
    routine messages from appearing after the shell prompt returns.
    """
    # Keep errors visible, suppress INFO/WARNING
    for handler in logging.root.handlers:
        handler.setLevel(logging.ERROR)

    # Also suppress uvicorn loggers
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setLevel(logging.ERROR)

    # Quiet the Rich console
    if _rich_console:
        _rich_console.quiet = True


setup_logging()
logger = logging.getLogger(__name__)


def run_migrations() -> None:
    """Run database migrations using Alembic."""
    alembic_ini = files("yubal_api").joinpath("alembic.ini")
    alembic_cfg = Config(str(alembic_ini))
    command.upgrade(alembic_cfg, "head")


def create_services(repository: SubscriptionRepository) -> Services:
    """Create all application services with proper dependency wiring.

    Args:
        repository: Repository for subscription database operations.

    Returns:
        Services container with all application services.
    """
    settings = get_settings()

    # Create event bus and log buffer
    job_event_bus = JobEventBus()
    log_buffer = LogBuffer()

    # Attach log streaming before services start logging
    setup_log_streaming(log_buffer)

    # Create shutdown coordinator
    shutdown_coordinator = ShutdownCoordinator()

    # Create job management services
    job_store = JobStore(
        clock=lambda: datetime.now(settings.timezone),
        id_generator=lambda: str(uuid.uuid4()),
        event_bus=job_event_bus,
    )

    # Create subscription service
    cookies_path = settings.cookies_file if settings.cookies_file.exists() else None
    playlist_info = PlaylistInfoService(cookies_path=cookies_path)
    subscription_service = SubscriptionService(
        repository=repository,
        playlist_info=playlist_info,
        data_path=settings.data,
        ascii_filenames=settings.ascii_filenames,
    )

    preferences_store = PreferencesStore(
        settings.preferences_file,
        settings.data,
        defaults=preferences_from_settings(settings),
    )

    track_catalog = TrackCatalogRepository(repository.engine)
    membership_service = SubscriptionMembershipService(
        membership_repo=SubscriptionMembershipRepository(repository.engine),
        snapshot_repo=SubscriptionSnapshotRepository(repository.engine),
        subscription_repo=repository,
        track_catalog=track_catalog,
        data_path=settings.data,
        archive_folder=preferences_store.effective().direct_folder,
    )
    subscription_service.bind_membership(membership_service)

    sync_ledger_service = SyncLedgerService(
        SyncLedgerRepository(repository.engine),
        data_path=settings.data,
        preferences_store=preferences_store,
        track_catalog=track_catalog,
    )

    preview_root = (
        DOWNLOAD_CACHE_ROOT / "SearchPreview"
        if DOWNLOAD_CACHE_ROOT.is_dir()
        else settings.temp / "SearchPreview"
    )
    search_service = SearchService(
        state_path=settings.config / "search_results.json",
        preview_root=preview_root,
        data_path=settings.data,
        cookies_path=cookies_path,
        preferences=preferences_store,
        track_catalog=track_catalog,
        sync_ledger=sync_ledger_service,
    )

    preselect_service = PreselectService(
        PreselectRepository(repository.engine),
        preferences_store,
        settings.data,
    )
    wash_service = WashService(
        preselect_service,
        TrackCatalogRepository(repository.engine),
        settings.data,
    )

    operation_gate = OperationGate()

    library_health = LibraryHealthService(settings.config / "library_health.json")
    library_health.bind_require_external(
        lambda: preferences_store.effective().external_library_enabled
    )
    operation_gate.bind_health(library_health)

    external_repository = ExternalLibraryRepository(repository.engine)
    external_library_service = ExternalLibraryService(
        external_repository,
        track_catalog,
        preferences_store,
        cookies_path=cookies_path,
    )
    sync_ledger_service.bind_external_library(external_library_service)
    membership_service.bind_external_library(external_library_service)

    wanted_repository = WantedRepository(repository.engine)
    wanted_service = WantedService(
        wanted_repository,
        preferences_store,
        cookies_path=cookies_path,
        sync_ledger=sync_ledger_service,
        external_library=external_library_service,
    )
    sync_ledger_service.bind_wanted_service(wanted_service)
    membership_service.bind_wanted_service(wanted_service)
    external_library_service.bind_wanted_service(wanted_service)
    library_dedup_service = LibraryDedupService(track_catalog)
    library_lookup_service = LibraryLookupService(
        catalog=track_catalog,
        subscriptions=subscription_service,
        preferences=preferences_store,
        sync_ledger=sync_ledger_service,
    )
    library_stats_service = LibraryStatsService(
        catalog=track_catalog,
        external=external_repository,
        wanted=wanted_repository,
        download_root=settings.data,
        external_root=EXTERNAL_ROOT,
        wanted_root=WANTED_ROOT,
    )

    folder_presence = CatalogFolderPresence(track_catalog, settings.data)

    track_retag_service = TrackRetagService(
        track_catalog,
        settings.data,
        ascii_filenames=settings.ascii_filenames,
    )
    cookies_path = settings.cookies_file if settings.cookies_file.exists() else None
    track_metadata_service = TrackMetadataService(
        catalog=track_catalog,
        cookies_path=cookies_path,
        preferences=preferences_store,
    )
    library_enrichment_service = LibraryEnrichmentService(
        catalog=track_catalog,
        data_path=settings.data,
        preferences=preferences_store,
        cookies_path=cookies_path,
    )
    external_library_service.bind_enrichment(library_enrichment_service)

    sync_pipeline_service = SyncPipelineService(
        library_health=library_health,
        external_library_service=external_library_service,
        library_enrichment_service=library_enrichment_service,
        library_dedup_service=library_dedup_service,
        preferences_store=preferences_store,
        sync_ledger_service=sync_ledger_service,
        wanted_service=wanted_service,
    )
    sync_ledger_service.bind_post_job_finalize(
        lambda folder, kind: sync_pipeline_service.sync_catalog_folder(
            folder,
            trigger=f"{kind}:job",
        )
    )

    job_executor = JobExecutor(
        job_store=job_store,
        base_path=settings.data,
        audio_format=settings.audio_format,
        audio_quality=int(settings.audio_quality),
        cookies_path=settings.cookies_file,
        fetch_lyrics=settings.fetch_lyrics,
        ytmusic_lyrics_fallback=settings.ytmusic_lyrics_fallback,
        qq_lyrics_fallback=settings.qq_lyrics_fallback,
        apply_replaygain=settings.replaygain,
        ascii_filenames=settings.ascii_filenames,
        download_ugc=settings.download_ugc,
        subscription_service=subscription_service,
        membership_service=membership_service,
        sync_ledger_service=sync_ledger_service,
        preferences_store=preferences_store,
        cache_path=settings.cache_path,
        job_timeout=settings.job_timeout_seconds,
        operation_gate=operation_gate,
        folder_presence=folder_presence,
    )
    wanted_service.bind_job_executor(job_executor)

    # Create scheduler
    scheduler_service = Scheduler(
        subscription_service=subscription_service,
        job_executor=job_executor,
        settings=settings,
        preferences_store=preferences_store,
        operation_gate=operation_gate,
        sync_ledger_service=sync_ledger_service,
        library_health=library_health,
        external_library_service=external_library_service,
        membership_service=membership_service,
        library_enrichment_service=library_enrichment_service,
        library_dedup_service=library_dedup_service,
        wanted_service=wanted_service,
        sync_pipeline_service=sync_pipeline_service,
    )

    subscription_service.bind_maintenance(operation_gate, job_executor)
    sync_ledger_service.bind_maintenance(operation_gate, job_executor)

    def _subscription_folder(subscription_id: UUID) -> str | None:
        try:
            sub = subscription_service.get(subscription_id)
        except Exception:
            return None
        return sub.save_folder or sub.name

    sync_ledger_service.bind_subscription_folders(_subscription_folder)

    # Wire up coordinator with executor
    shutdown_coordinator.set_job_executor(job_executor)

    telegram_bot = TelegramBotService(
        preferences=preferences_store,
        catalog=track_catalog,
        library_lookup=library_lookup_service,
        search=search_service,
        playlist_info=playlist_info,
        job_executor=job_executor,
        job_store=job_store,
        subscriptions=subscription_service,
        data_path=settings.data,
        config_path=settings.config,
        tg_api_url=settings.tg_api_url,
        scheduler=scheduler_service,
        library_health=library_health,
        db_engine=repository.engine,
        wanted_service=wanted_service,
    )

    return Services(
        job_store=job_store,
        job_executor=job_executor,
        shutdown_coordinator=shutdown_coordinator,
        subscription_service=subscription_service,
        membership_service=membership_service,
        sync_ledger_service=sync_ledger_service,
        sync_pipeline_service=sync_pipeline_service,
        preferences_store=preferences_store,
        scheduler=scheduler_service,
        job_event_bus=job_event_bus,
        log_buffer=log_buffer,
        operation_gate=operation_gate,
        preselect_service=preselect_service,
        wash_service=wash_service,
        search_service=search_service,
        track_retag_service=track_retag_service,
        track_metadata_service=track_metadata_service,
        library_enrichment_service=library_enrichment_service,
        library_health=library_health,
        external_library_service=external_library_service,
        library_dedup_service=library_dedup_service,
        library_lookup_service=library_lookup_service,
        library_stats_service=library_stats_service,
        telegram_bot=telegram_bot,
        playlist_info=playlist_info,
        wanted_service=wanted_service,
    )


def create_api_router() -> APIRouter:
    """Create the API router with all routes under /api prefix."""
    base_path = get_settings().base_path
    api_router = APIRouter(prefix=f"{base_path}/api")
    api_router.include_router(health.router)
    api_router.include_router(auth.router)
    api_router.include_router(info.router)
    api_router.include_router(search_routes.router)
    api_router.include_router(jobs.router)
    api_router.include_router(logs.router)
    api_router.include_router(cookies.router)
    api_router.include_router(subscriptions.router)
    api_router.include_router(library.router)
    api_router.include_router(sync_ledger.router)
    api_router.include_router(settings_routes.router)
    api_router.include_router(external.router)
    api_router.include_router(wanted_routes.router)
    api_router.include_router(scheduler.router)
    return api_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler for startup/shutdown."""
    settings = get_settings()
    logger.info("Starting application...")

    auth_manager: AuthManager = app.state.auth
    if auth_manager.enabled:
        logger.info("Built-in auth enabled (%s)", settings.auth_file)
    else:
        logger.info("Built-in auth disabled (use external auth if needed)")

    # Automatic migrations are preceded by a verified online SQLite snapshot.
    # A failed backup blocks startup so schema changes never proceed without a
    # recoverable database copy.
    backup = await asyncio.to_thread(
        create_verified_database_backup,
        settings.db_path,
        settings.db_path.parent / "backups",
    )
    if backup is not None:
        logger.info(
            "Verified pre-migration database backup: %s (%d bytes)",
            backup.path,
            backup.size_bytes,
        )

    # Run database migrations (in thread to avoid blocking event loop)
    await asyncio.to_thread(run_migrations)
    await asyncio.to_thread(verify_sqlite_database, settings.db_path)
    logger.info("Database migrations complete")

    # Create database engine
    db_path = settings.db_path
    engine = create_db_engine(db_path)

    # Create services with database repository
    repository = SubscriptionRepository(engine)
    services = create_services(repository)
    app.state.services = services
    logger.info("Services initialized")

    # Ensure External/Raw + External/Organized exist (no-op if unmounted), then
    # probe mount health so jobs/scheduler start with an accurate status.
    await asyncio.to_thread(ensure_external_layout)
    await asyncio.to_thread(ensure_wanted_layout)
    health = await asyncio.to_thread(services.library_health.check)
    if not health.ok:
        logger.warning(
            "Library health at startup: %s (%s)", health.status, health.reason
        )

    # One-shot rename of legacy "NN - Title" filenames → "Artist - Title"
    if health.ok:
        from yubal_api.services.naming_migration_service import NamingConventionMigrator

        migrator = NamingConventionMigrator(
            TrackCatalogRepository(repository.engine),
            settings.data,
            ascii_filenames=settings.ascii_filenames,
        )
        naming = await asyncio.to_thread(migrator.run)
        if naming.renamed or naming.errors:
            logger.info(
                "Naming migration: renamed=%d errors=%d",
                naming.renamed,
                naming.errors,
            )

    save_folders = [sub.save_folder or sub.name for sub in repository.list()]
    repaired = repair_track_index(settings.data, save_folders=save_folders)
    if repaired:
        logger.info("Repaired %d stale track index entries on startup", repaired)

    # Jobs are in-memory: a restart mid-sync leaves ledger rows stuck at
    # "running". Resolve them to "interrupted" so the UI never mislabels a
    # previously-synced folder as "never run".
    interrupted = services.sync_ledger_service.reconcile_interrupted_jobs()
    if interrupted:
        logger.info("Marked %d interrupted sync ledger row(s) on startup", interrupted)

    # Leftover .part / abandoned Cache staging from crashed downloads.
    cache_root = DOWNLOAD_CACHE_ROOT if DOWNLOAD_CACHE_ROOT.is_dir() else None
    temps = cleanup_startup_temps(DOWNLOAD_ROOT, cache_root)
    if EXTERNAL_ROOT.is_dir() and EXTERNAL_ROOT.resolve() != DOWNLOAD_ROOT.resolve():
        temps["part_files"] += cleanup_part_files(EXTERNAL_ROOT)
    if temps["part_files"] or temps["staging_files"]:
        logger.info(
            "Startup temp cleanup: part_files=%d staging_files=%d",
            temps["part_files"],
            temps["staging_files"],
        )

    # Start scheduler
    services.scheduler.start()

    # Start Telegram bot when token is configured
    await services.telegram_bot.start()

    yield

    # Shutdown sequence
    await services.telegram_bot.stop()

    # Stop scheduler first
    await services.scheduler.stop()

    # Cancel any running jobs
    services.shutdown_coordinator.begin_shutdown()

    # Suppress logging to prevent post-prompt messages
    suppress_logging()

    # Clean up .part files from incomplete downloads (delegated to yubal)
    cleanup_part_files(settings.data)

    # Clean temp directory
    if settings.temp.exists():
        shutil.rmtree(settings.temp, ignore_errors=True)

    services.close()


def custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Generate OpenAPI schema with SSE event types included.

    SSE event schemas aren't auto-discovered by FastAPI since they're
    returned via StreamingResponse. This function injects them into
    the OpenAPI schema so TypeScript types are generated.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )

    # Inject SSE event schemas (not auto-discovered due to StreamingResponse)
    sse_models = [
        # Jobs SSE events
        (SnapshotEvent, "SnapshotEvent"),
        (CreatedEvent, "CreatedEvent"),
        (UpdatedEvent, "UpdatedEvent"),
        (DeletedEvent, "DeletedEvent"),
        (ClearedEvent, "ClearedEvent"),
        # Logs SSE event
        (LogEntry, "LogEntry"),
    ]
    for model, name in sse_models:
        json_schema = TypeAdapter(model).json_schema(
            ref_template="#/components/schemas/{model}"
        )
        defs = json_schema.pop("$defs", {})
        schema["components"]["schemas"].update(defs)
        schema["components"]["schemas"][name] = json_schema

    # Define SSE endpoint response schemas
    base_path = get_settings().base_path
    sse_endpoints = {
        f"{base_path}/api/jobs/sse": {
            "schema": {
                "oneOf": [
                    {"$ref": "#/components/schemas/SnapshotEvent"},
                    {"$ref": "#/components/schemas/CreatedEvent"},
                    {"$ref": "#/components/schemas/UpdatedEvent"},
                    {"$ref": "#/components/schemas/DeletedEvent"},
                    {"$ref": "#/components/schemas/ClearedEvent"},
                ]
            },
        },
        f"{base_path}/api/logs/sse": {
            "schema": {"$ref": "#/components/schemas/LogEntry"},
        },
    }

    # Inject response schemas into SSE endpoints
    for path, config in sse_endpoints.items():
        if path in schema["paths"]:
            schema["paths"][path]["get"]["responses"]["200"]["content"] = {
                "text/event-stream": {
                    "schema": config["schema"],
                }
            }

    app.openapi_schema = schema
    return schema


class YubalFastAPI(FastAPI):
    """FastAPI app with custom OpenAPI schema including SSE event types."""

    def openapi(self) -> dict[str, Any]:
        return custom_openapi(self)


class SPAStaticFiles(StaticFiles):
    """SPA static files with base path injection into index.html."""

    def __init__(
        self, *, base_path: str = "", directory: Path | str, **kwargs: Any
    ) -> None:
        super().__init__(directory=directory, **kwargs)
        self._base_path = base_path
        self._dir = Path(directory)
        self._cached_index: str | None = None
        self._cached_mtime_ns: int | None = None

    def _get_index_html(self) -> str:
        """Get index.html with base path injected; reload when file changes."""
        index_path = self._dir / "index.html"
        mtime_ns = index_path.stat().st_mtime_ns
        if self._cached_index is None or self._cached_mtime_ns != mtime_ns:
            html = index_path.read_text()
            base_href = f"{self._base_path}/" if self._base_path else "/"
            html = re.sub(
                r'<base\s+href="/"\s*/?>',
                f'<base href="{base_href}">',
                html,
                count=1,
            )
            self._cached_index = html
            self._cached_mtime_ns = mtime_ns
        return self._cached_index

    async def get_response(self, path: str, scope: Scope) -> Response:
        """Serve static files, falling back to index.html for SPA routes."""
        if path == "." or path == "index.html":
            return HTMLResponse(self._get_index_html())
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as ex:
            if ex.status_code == 404:
                return HTMLResponse(self._get_index_html())
            raise


def create_app() -> FastAPI:
    """Create and configure the main FastAPI application."""
    settings = get_settings()
    base_path = settings.base_path

    app = YubalFastAPI(
        title="yubal",
        description="YouTube Music Downloader API",
        version=version("yubal_api"),
        lifespan=lifespan,
        debug=settings.debug,
        docs_url=f"{base_path}/docs",
        redoc_url=f"{base_path}/redoc",
        openapi_url=f"{base_path}/openapi.json",
    )

    # Register exception handlers
    register_exception_handlers(app)

    # Built-in auth (created early so middleware can use it; state also set in lifespan)
    auth_manager = AuthManager(
        enabled=settings.auth_login,
        auth_file=settings.auth_file,
    )
    app.state.auth = auth_manager
    app.add_middleware(
        AuthMiddleware,  # type: ignore[arg-type]
        auth=auth_manager,
        base_path=base_path,
    )

    # CORS middleware (type ignore needed due to Starlette typing limitations)
    app.add_middleware(
        CORSMiddleware,  # type: ignore[arg-type]
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes under /api prefix
    app.include_router(create_api_router())

    # Static files from YUBAL_ROOT/web/dist
    # Fix MIME types for Windows (registry defaults .js to text/plain)
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")

    web_build = settings.root / "web" / "dist"
    if web_build.exists():
        mount_path = f"{base_path}/" if base_path else "/"
        app.mount(
            mount_path,
            SPAStaticFiles(base_path=base_path, directory=web_build, html=True),
            name="spa",
        )

    return app


# Create app instance for uvicorn
app = create_app()
