import { UrlInput } from "@/components/common/url-input";
import { getContentInfo } from "@/api/info";
import {
  deleteExternalPlaylist,
  listExternalPlaylists,
  type ExternalDeleteMode,
  type ExternalPlaylist,
} from "@/api/external";
import {
  lookupPlaylistPresence,
  lookupTextPresence,
  lookupTrackPresence,
  type LibraryLocationHit,
  type TextMatchHit,
} from "@/api/library-lookup";
import {
  getLibraryTrackSummary,
  type LibraryTrackSummary,
} from "@/api/library";
import {
  deleteSearchResults,
  getSearchResults,
  searchSongs,
  type SearchSnapshot,
  type SearchTrack,
} from "@/api/search";
import {
  SEARCH_FOLDER,
  SearchResultsCard,
} from "@/features/search/search-results-card";
import { LogsPanel } from "@/features/logs/logs-panel";
import { ExternalPlaylistDeleteModal } from "@/features/sync/external-playlist-delete-modal";
import { ExternalPlaylistEditModal } from "@/features/sync/external-playlist-edit-modal";
import { LedgerPanel } from "@/features/sync/ledger-panel";
import { SchedulerBar } from "@/features/sync/scheduler-bar";
import { DirectDeleteModal } from "@/features/sync/direct-delete-modal";
import { DirectEditModal } from "@/features/sync/direct-edit-modal";
import {
  SubscriptionDeleteModal,
  type DeleteFileAction,
} from "@/features/sync/subscription-delete-modal";
import { SubscriptionEditModal } from "@/features/sync/subscription-edit-modal";
import { useJobs } from "@/features/jobs/jobs-context";
import { useSubscriptions } from "@/features/subscriptions/use-subscriptions";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import {
  deleteDirect,
  listSyncLedger,
  reconcileDirect,
  syncDirect,
  updateDirect,
  type DirectPlaylistDeleteMode,
  type DirectPolicyUpdates,
  type SyncLedgerEntry,
} from "@/api/sync-ledger";
import {
  clearSubscriptionOffline,
  type Subscription,
  type SyncStepResult,
} from "@/api/subscriptions";
import { getSettings } from "@/api/settings";
import { getWantedSummary, type WantedSummary } from "@/api/wanted";
import { cardActionClass, cardShadow, layout } from "@/lib/ui-styles";
import { classifyUnifiedInput } from "@/lib/url";
import {
  Button,
  Card,
  CardBody,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
} from "@heroui/react";
import { DownloadIcon, SearchIcon, ZapIcon } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const DEFAULT_MAX_ITEMS = 50;
const DEFAULT_DIRECT_LIMIT = 50;
const DIRECT_EXPAND_KEY = "direct";

function trackWatchUrl(videoId: string): string {
  return `https://music.youtube.com/watch?v=${videoId}`;
}

export function JobsPage() {
  const { t } = useTranslation();
  const { jobs, isLoading: jobsLoading, startJob, cancelJob } = useJobs();
  const {
    subscriptions,
    schedulerStatus,
    isLoading: subsLoading,
    addSubscription,
    updateSubscription,
    deleteSubscription,
    syncSubscription,
    syncAll,
    refresh: refreshSubscriptions,
    refreshScheduler,
  } = useSubscriptions();

  const [url, setUrl] = useState("");
  const [syncSteps, setSyncSteps] = useState<SyncStepResult[]>([]);
  const [directLimitExceeded, setDirectLimitExceeded] = useState<{
    url: string;
    trackCount: number;
    limit: number;
  } | null>(null);
  const [inputChoice, setInputChoice] = useState<{
    url: string;
    trackCount: number;
    limit: number;
  } | null>(null);
  /** Elsewhere → one-click: 是 (add to Direct) | 歌单… (expand). */
  const [elsewhereChoice, setElsewhereChoice] = useState<{
    url: string;
    locations: LibraryLocationHit[];
  } | null>(null);
  /** Text local fuzzy hits + optional online search. */
  const [textMatchChoice, setTextMatchChoice] = useState<{
    query: string;
    matches: TextMatchHit[];
  } | null>(null);
  const [ledger, setLedger] = useState<SyncLedgerEntry[]>([]);
  const [ledgerLoading, setLedgerLoading] = useState(true);
  const [externalPlaylists, setExternalPlaylists] = useState<
    ExternalPlaylist[]
  >([]);
  const [externalEnabled, setExternalEnabled] = useState(false);
  const [externalLoading, setExternalLoading] = useState(true);
  const [wantedSummary, setWantedSummary] = useState<WantedSummary | null>(
    null,
  );
  const [wantedEnabled, setWantedEnabled] = useState(false);
  const [editingExternal, setEditingExternal] =
    useState<ExternalPlaylist | null>(null);
  const [deletingExternal, setDeletingExternal] =
    useState<ExternalPlaylist | null>(null);
  const [isDirecting, setIsDirecting] = useState(false);
  const [isSearching, setIsSearching] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const [isInspecting, setIsInspecting] = useState(false);
  const [directDownloadLimit, setDirectDownloadLimit] =
    useState(DEFAULT_DIRECT_LIMIT);
  const [isSyncingAll, setIsSyncingAll] = useState(false);
  const [editing, setEditing] = useState<Subscription | null>(null);
  const [deleting, setDeleting] = useState<Subscription | null>(null);
  const [editingDirect, setEditingDirect] = useState(false);
  const [deletingDirect, setDeletingDirect] = useState(false);
  const [searchSnapshot, setSearchSnapshot] = useState<SearchSnapshot | null>(
    null,
  );
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [librarySummary, setLibrarySummary] =
    useState<LibraryTrackSummary | null>(null);
  const previousJobStatusesRef = useRef<Map<string, string> | null>(null);

  const canAct = url.trim().length > 0;
  const inputKind = classifyUnifiedInput(url);
  const inputBusy = isDirecting || isAdding || isSearching || isInspecting;
  const directEntry = useMemo(
    () => ledger.find((entry) => entry.kind === "direct") ?? null,
    [ledger],
  );
  const directFolder = directEntry?.save_folder ?? "direct";

  const refreshLedger = useCallback(async () => {
    const items = await listSyncLedger();
    setLedger(items);
    setLedgerLoading(false);
  }, []);

  const refreshLibrarySummary = useCallback(async () => {
    setLibrarySummary(await getLibraryTrackSummary());
  }, []);

  const refreshExternal = useCallback(async () => {
    const settings = await getSettings();
    const enabled = Boolean(settings?.external_library_enabled);
    setExternalEnabled(enabled);
    if (!enabled) {
      setExternalPlaylists([]);
      setExternalLoading(false);
      return;
    }
    const items = await listExternalPlaylists();
    setExternalPlaylists(items);
    setExternalLoading(false);
  }, []);

  const refreshWanted = useCallback(async () => {
    const settings = await getSettings();
    const enabled = Boolean(settings?.wanted_enabled ?? true);
    setWantedEnabled(enabled);
    if (!enabled) {
      setWantedSummary(null);
      return;
    }
    setWantedSummary(await getWantedSummary());
  }, []);

  const refreshSearch = useCallback(async () => {
    setSearchSnapshot(await getSearchResults());
  }, []);

  const refreshDirectDownloadLimit = useCallback(async () => {
    const settings = await getSettings();
    if (settings) setDirectDownloadLimit(settings.direct_download_limit);
  }, []);

  useEffect(() => {
    void refreshLedger();
    void refreshLibrarySummary();
    void refreshSearch();
    void refreshDirectDownloadLimit();
    void refreshExternal();
    void refreshWanted();
  }, [
    refreshDirectDownloadLimit,
    refreshExternal,
    refreshLedger,
    refreshLibrarySummary,
    refreshSearch,
    refreshWanted,
  ]);

  useEffect(() => {
    const onSettingsChanged = () => {
      void refreshSubscriptions();
      void refreshDirectDownloadLimit();
      void refreshExternal();
      void refreshLibrarySummary();
      void refreshWanted();
    };
    window.addEventListener("yubal:settings-changed", onSettingsChanged);
    return () => {
      window.removeEventListener("yubal:settings-changed", onSettingsChanged);
    };
  }, [
    refreshDirectDownloadLimit,
    refreshExternal,
    refreshLibrarySummary,
    refreshSubscriptions,
    refreshWanted,
  ]);

  useEffect(() => {
    const onLedgerChanged = (event: Event) => {
      const detail = (event as CustomEvent<{ skipPageRefresh?: boolean }>)
        .detail;
      if (detail?.skipPageRefresh) return;
      void refreshLedger();
      void refreshLibrarySummary();
      void refreshExternal();
      void refreshWanted();
    };
    window.addEventListener("yubal:ledger-changed", onLedgerChanged);
    return () => {
      window.removeEventListener("yubal:ledger-changed", onLedgerChanged);
    };
  }, [refreshExternal, refreshLedger, refreshLibrarySummary, refreshWanted]);

  const lastDataUpdatedAt = useMemo(() => {
    let max: string | null = null;
    const consider = (value: string | null | undefined) => {
      if (!value) return;
      if (!max || value > max) max = value;
    };
    for (const entry of ledger) {
      consider(entry.last_synced_at);
      consider(entry.updated_at);
    }
    for (const sub of subscriptions) {
      consider(sub.last_synced_at);
    }
    return max;
  }, [ledger, subscriptions]);

  useEffect(() => {
    const next = new Map(jobs.map((job) => [job.id, job.status]));
    const previous = previousJobStatusesRef.current;
    previousJobStatusesRef.current = next;
    if (previous === null) return;

    const becameTerminal = jobs.some((job) => {
      const oldStatus = previous.get(job.id);
      if (!oldStatus) return false;
      const wasActive =
        oldStatus === "pending" ||
        oldStatus === "fetching_info" ||
        oldStatus === "downloading" ||
        oldStatus === "importing";
      const isTerminal =
        job.status === "completed" ||
        job.status === "failed" ||
        job.status === "cancelled";
      return wasActive && isTerminal;
    });
    if (!becameTerminal) return;

    void Promise.all([
      refreshLedger(),
      refreshLibrarySummary(),
      refreshSubscriptions(),
      refreshSearch(),
      refreshWanted(),
      refreshExternal(),
    ]).then(() => {
      window.dispatchEvent(
        new CustomEvent("yubal:ledger-changed", {
          detail: { skipPageRefresh: true },
        }),
      );
    });
  }, [
    jobs,
    refreshExternal,
    refreshLedger,
    refreshLibrarySummary,
    refreshSearch,
    refreshSubscriptions,
    refreshWanted,
  ]);

  const requireUrl = () => {
    if (inputKind === "ytm_url") return true;
    showErrorToast(t("search.invalidInputTitle"), t("search.urlRequired"));
    return false;
  };

  const expandPlaylist = (expandKey: string) => {
    setExpandedKey(expandKey);
  };

  const applyTrackPresence = async (
    presence: {
      in_direct: boolean;
      locations: LibraryLocationHit[];
    },
    downloadUrl: string,
  ): Promise<"handled" | "continue"> => {
    if (presence.in_direct) {
      expandPlaylist(DIRECT_EXPAND_KEY);
      showSuccessToast(
        t("search.alreadyLocalTitle"),
        t("search.alreadyInDirect"),
      );
      setUrl("");
      return "handled";
    }
    if (presence.locations.length > 0) {
      setElsewhereChoice({
        url: downloadUrl,
        locations: presence.locations,
      });
      return "handled";
    }
    return "continue";
  };

  const runOnlineSearch = async (query: string) => {
    if (isSearching) return;
    setIsSearching(true);
    const result = await searchSongs(query);
    setIsSearching(false);
    if ("error" in result) {
      showErrorToast(t("search.failed"), result.error);
      return;
    }
    setSearchSnapshot(result.data);
    setExpandedKey(SEARCH_FOLDER);
    setUrl("");
  };

  const handleSearch = async () => {
    if (!canAct || isSearching) return;
    if (inputKind !== "text") {
      showErrorToast(t("search.invalidInputTitle"), t("search.textRequired"));
      return;
    }
    await runOnlineSearch(url.trim());
  };

  const continueMultiTrack = async (targetUrl: string, trackCount: number) => {
    if (trackCount <= directDownloadLimit) {
      setInputChoice({
        url: targetUrl,
        trackCount,
        limit: directDownloadLimit,
      });
      return;
    }
    await subscribeUrl(targetUrl);
  };

  const startDirectUrl = async (targetUrl: string) => {
    if (isDirecting) return false;
    setIsDirecting(true);
    const result = await startJob(targetUrl);
    setIsDirecting(false);
    if (!result.success) {
      if (
        result.code === "direct_download_limit_exceeded" &&
        result.trackCount &&
        result.limit
      ) {
        setDirectLimitExceeded({
          url: targetUrl,
          trackCount: result.trackCount,
          limit: result.limit,
        });
      }
      return false;
    }
    setUrl("");
    window.setTimeout(() => {
      void refreshLedger();
    }, 500);
    return true;
  };

  const handleDirect = async () => {
    if (!canAct || !requireUrl()) return;
    await startDirectUrl(url.trim());
  };

  const handleSubscribeFromLimit = async () => {
    if (!directLimitExceeded) return;
    setIsAdding(true);
    const success = await addSubscription(
      directLimitExceeded.url,
      DEFAULT_MAX_ITEMS,
    );
    setIsAdding(false);
    if (!success) return;
    setUrl("");
    setDirectLimitExceeded(null);
    await refreshLedger();
  };

  const subscribeUrl = async (targetUrl: string) => {
    if (isAdding) return false;
    setIsAdding(true);
    const success = await addSubscription(targetUrl, DEFAULT_MAX_ITEMS);
    if (success) {
      setUrl("");
      await refreshLedger();
    }
    setIsAdding(false);
    return success;
  };

  const handleSubscribe = async () => {
    if (!canAct || !requireUrl()) return;
    await subscribeUrl(url.trim());
  };

  const handleUnifiedSubmit = async () => {
    if (isSearching || isDirecting || isAdding || isInspecting) return;
    const kind = classifyUnifiedInput(url);
    if (kind === "text") {
      const query = url.trim();
      setIsInspecting(true);
      const local = await lookupTextPresence(query);
      setIsInspecting(false);
      if ("error" in local) {
        // Lookup failure should not block online search.
        await runOnlineSearch(query);
        return;
      }
      if (local.matches.length > 0) {
        setTextMatchChoice({ query, matches: local.matches });
        return;
      }
      await runOnlineSearch(query);
      return;
    }
    if (kind !== "ytm_url") {
      showErrorToast(
        t("search.invalidInputTitle"),
        t("search.invalidUnifiedInput"),
      );
      return;
    }

    const targetUrl = url.trim();
    setIsInspecting(true);
    const info = await getContentInfo(targetUrl);
    if ("error" in info) {
      setIsInspecting(false);
      showErrorToast(t("search.invalidInputTitle"), info.error);
      return;
    }

    // The backend reports null for a single track; jobs already treats it as 1.
    const trackCount = Math.max(1, info.track_count ?? 1);
    const isSingle = info.kind === "track" || trackCount === 1;

    if (isSingle) {
      const videoId = info.playlist_id || "";
      const presence = videoId ? await lookupTrackPresence(videoId) : null;
      setIsInspecting(false);
      if (presence && !("error" in presence)) {
        const outcome = await applyTrackPresence(presence, targetUrl);
        if (outcome === "handled") return;
      }
      await startDirectUrl(targetUrl);
      return;
    }

    const playlist = await lookupPlaylistPresence(targetUrl);
    setIsInspecting(false);
    if (!("error" in playlist)) {
      if (playlist.subscription) {
        expandPlaylist(playlist.subscription.expand_key);
        showSuccessToast(
          t("search.alreadyLocalTitle"),
          playlist.subscription.enabled === false
            ? t("search.alreadySubscribedDisabled", {
                name: playlist.subscription.title,
              })
            : t("search.alreadySubscribed", {
                name: playlist.subscription.title,
              }),
        );
        setUrl("");
        return;
      }
      if (playlist.in_direct_url) {
        expandPlaylist(DIRECT_EXPAND_KEY);
        showSuccessToast(
          t("search.alreadyLocalTitle"),
          t("search.alreadyInDirectPlaylist"),
        );
        setUrl("");
        return;
      }
    }
    await continueMultiTrack(targetUrl, trackCount);
  };

  const handleElsewhereYes = async () => {
    if (!elsewhereChoice) return;
    const success = await startDirectUrl(elsewhereChoice.url);
    if (success) setElsewhereChoice(null);
  };

  const handleElsewhereExpand = (expandKey: string) => {
    expandPlaylist(expandKey);
    setElsewhereChoice(null);
    setUrl("");
  };

  const handleTextMatchOnline = async () => {
    if (!textMatchChoice) return;
    const query = textMatchChoice.query;
    setTextMatchChoice(null);
    await runOnlineSearch(query);
  };

  const handleTextMatchSelect = async (match: TextMatchHit) => {
    setTextMatchChoice(null);
    const downloadUrl = trackWatchUrl(match.video_id);
    const outcome = await applyTrackPresence(match, downloadUrl);
    if (outcome === "continue") {
      await startDirectUrl(downloadUrl);
    }
  };

  const handleChoiceDirect = async () => {
    if (!inputChoice) return;
    const choice = inputChoice;
    const success = await startDirectUrl(choice.url);
    if (success) setInputChoice(null);
  };

  const handleChoiceSubscribe = async () => {
    if (!inputChoice) return;
    const success = await subscribeUrl(inputChoice.url);
    if (success) setInputChoice(null);
  };

  const handleSyncAll = async () => {
    setIsSyncingAll(true);
    const steps = await syncAll();
    if (steps) setSyncSteps(steps);
    await reconcileDirect();
    await refreshLedger();
    await refreshSubscriptions();
    setIsSyncingAll(false);
  };

  const handleRowSync = async (id: string) => {
    await syncSubscription(id);
    window.setTimeout(() => {
      void refreshLedger();
    }, 500);
  };

  const handleDirectReconcile = async () => {
    await syncDirect();
    await refreshLedger();
  };

  const handleRowDelete = async (action: DeleteFileAction) => {
    if (!deleting) return false;
    if (
      action.startsWith("clear_offline") ||
      action.startsWith("clear_id_invalid")
    ) {
      const mode = action.endsWith("to_raw_delete")
        ? "to_raw_delete"
        : action.endsWith("to_wanted")
          ? "to_wanted"
          : "delete";
      const status = action.startsWith("clear_id_invalid")
        ? "id_invalid"
        : "offline";
      const result = await clearSubscriptionOffline(deleting.id, mode, status);
      if (result) {
        window.dispatchEvent(new Event("yubal:ledger-changed"));
        return true;
      }
      return false;
    }
    const ok = await deleteSubscription(
      deleting.id,
      action === "keep_list" ||
        action === "move_to_direct" ||
        action === "delete"
        ? action
        : "keep",
    );
    if (ok) {
      await refreshLedger();
      void refreshLibrarySummary();
    }
    return ok;
  };

  const handleDirectFolderSave = async (
    updates: DirectPolicyUpdates & { save_folder: string },
    confirmMove: boolean,
  ) => {
    const result = await updateDirect(updates, confirmMove);
    if (result === "ok") await refreshLedger();
    return result;
  };

  const handleDirectDelete = async (mode: DirectPlaylistDeleteMode) => {
    const ok = await deleteDirect(true, mode);
    if (ok) {
      window.dispatchEvent(new Event("yubal:ledger-changed"));
    }
    return ok;
  };

  const handleDeleteSearch = async () => {
    if (await deleteSearchResults()) {
      setSearchSnapshot(null);
      setExpandedKey((current) => (current === SEARCH_FOLDER ? null : current));
    }
  };

  const handleSearchTrackDownload = async (track: SearchTrack) => {
    const result = await startJob(
      `https://music.youtube.com/watch?v=${encodeURIComponent(track.video_id)}`,
    );
    if (result.success) return;
    if (
      result.code === "direct_download_limit_exceeded" &&
      result.trackCount &&
      result.limit
    ) {
      setDirectLimitExceeded({
        url: `https://music.youtube.com/watch?v=${track.video_id}`,
        trackCount: result.trackCount,
        limit: result.limit,
      });
    }
  };

  const enabledCount = subscriptions.filter((s) => s.enabled).length;
  const loading =
    jobsLoading || subsLoading || ledgerLoading || externalLoading;

  return (
    <>
      <SchedulerBar
        librarySummary={librarySummary}
        enabledCount={enabledCount}
        totalCount={subscriptions.length}
        schedulerStatus={schedulerStatus}
        lastDataUpdatedAt={lastDataUpdatedAt}
        isSyncing={isSyncingAll}
        syncSteps={syncSteps}
        canSyncAll={!isSyncingAll && !subsLoading}
        onSyncAll={() => {
          void handleSyncAll();
        }}
        onCountdownExpire={() => {
          void refreshScheduler();
        }}
      />

      <section
        className={`${layout.blockMargin} flex flex-col gap-3 md:flex-row md:flex-nowrap md:items-center md:gap-3`}
      >
        <div className="flex min-w-0 flex-1 flex-row items-center gap-2 sm:gap-3 md:contents">
          <div className="min-w-0 flex-1 md:min-w-[200px]">
            <UrlInput
              value={url}
              onChange={setUrl}
              onSubmit={() => {
                void handleUnifiedSubmit();
              }}
              disabled={inputBusy}
              placeholder={t("search.inputPlaceholder")}
              mode="mixed"
            />
          </div>
        </div>
        <div className="flex w-full flex-row gap-2 sm:gap-3 md:w-auto md:shrink-0">
          <Card
            shadow={cardShadow}
            isHoverable={!isSearching}
            isPressable={canAct && !isSearching}
            onPress={() => {
              if (!canAct || isSearching) return;
              void handleSearch();
            }}
            className={`${cardActionClass} flex-1 md:flex-none`}
          >
            <CardBody className="flex flex-row items-center justify-center gap-2 px-3 py-0 text-inherit sm:px-4">
              {isSearching ? (
                <Spinner size="sm" color="current" />
              ) : (
                <SearchIcon className="h-4 w-4 shrink-0" />
              )}
              <span className="truncate text-sm font-medium">
                {t("search.onlineSearch")}
              </span>
            </CardBody>
          </Card>
          <Card
            shadow={cardShadow}
            isHoverable={!isDirecting}
            isPressable={canAct && !isDirecting}
            onPress={() => {
              if (!canAct || isDirecting) return;
              void handleDirect();
            }}
            className={`${cardActionClass} flex-1 md:flex-none`}
          >
            <CardBody className="flex flex-row items-center justify-center gap-2 px-3 py-0 text-inherit sm:px-4">
              {isDirecting ? (
                <Spinner size="sm" color="current" />
              ) : (
                <DownloadIcon className="h-4 w-4 shrink-0" />
              )}
              <span className="truncate text-sm font-medium">
                {t("sync.directDownload")}
              </span>
            </CardBody>
          </Card>
          <Card
            shadow={cardShadow}
            isHoverable={!isAdding}
            isPressable={canAct && !isAdding}
            onPress={() => {
              if (!canAct || isAdding) return;
              void handleSubscribe();
            }}
            className={`${cardActionClass} flex-1 md:flex-none`}
          >
            <CardBody className="flex flex-row items-center justify-center gap-2 px-3 py-0 text-inherit sm:px-4">
              {isAdding ? (
                <Spinner size="sm" color="current" />
              ) : (
                <ZapIcon className="h-4 w-4 shrink-0" />
              )}
              <span className="truncate text-sm font-medium">
                {t("sync.addSubscription")}
              </span>
            </CardBody>
          </Card>
        </div>
      </section>

      <section className={`flex flex-col ${layout.blockGap}`}>
        {searchSnapshot ? (
          <SearchResultsCard
            key={searchSnapshot.searched_at}
            snapshot={searchSnapshot}
            tracksOpen={expandedKey === SEARCH_FOLDER}
            onToggleTracks={() =>
              setExpandedKey((current) =>
                current === SEARCH_FOLDER ? null : SEARCH_FOLDER,
              )
            }
            onCollapseTracks={() => setExpandedKey(null)}
            onSnapshotChange={setSearchSnapshot}
            onDelete={handleDeleteSearch}
            onDownload={handleSearchTrackDownload}
            onImported={() => {
              window.dispatchEvent(new Event("yubal:ledger-changed"));
            }}
            onExpired={() => {
              void handleDeleteSearch();
            }}
          />
        ) : null}
        <LedgerPanel
          entries={ledger}
          subscriptions={subscriptions}
          jobs={jobs}
          isLoading={loading}
          expandedKey={expandedKey}
          onExpandedKeyChange={setExpandedKey}
          onCancel={cancelJob}
          onEdit={setEditing}
          onSync={(id) => {
            void handleRowSync(id);
          }}
          onDelete={setDeleting}
          onEditDirect={() => setEditingDirect(true)}
          onDeleteDirect={() => setDeletingDirect(true)}
          onSyncDirect={() => {
            void handleDirectReconcile();
          }}
          onDirectTrackDeleted={(entry) => {
            setLedger((prev) => {
              const idx = prev.findIndex((item) => item.key === entry.key);
              if (idx < 0) return [...prev, entry];
              const next = [...prev];
              next[idx] = entry;
              return next;
            });
            void refreshLibrarySummary();
          }}
          schedulerEnabled={schedulerStatus?.enabled !== false}
          externalPlaylists={externalPlaylists}
          showExternalSection={externalEnabled}
          onEditExternal={setEditingExternal}
          onDeleteExternal={setDeletingExternal}
          onExternalChanged={() => {
            void refreshExternal();
            void refreshLibrarySummary();
          }}
          wantedSummary={wantedSummary}
          showWantedSection={wantedEnabled}
        />
        <LogsPanel jobs={jobs} />
      </section>

      <SubscriptionEditModal
        subscription={editing}
        isOpen={editing !== null}
        isSchedulerEnabled={schedulerStatus?.enabled}
        onClose={() => setEditing(null)}
        onSave={updateSubscription}
      />
      <SubscriptionDeleteModal
        subscription={deleting}
        isOpen={deleting !== null}
        externalEnabled={externalEnabled}
        wantedEnabled={wantedEnabled}
        onClose={() => setDeleting(null)}
        onConfirm={handleRowDelete}
      />
      <DirectEditModal
        isOpen={editingDirect}
        currentFolder={directFolder}
        initial={
          directEntry
            ? {
                save_folder: directEntry.save_folder,
                enabled: directEntry.enabled ?? false,
                max_items: directEntry.max_items ?? 100,
                sync_jitter_seconds: directEntry.sync_jitter_seconds ?? 600,
                offline_marking_enabled:
                  directEntry.offline_marking_enabled ?? true,
                offline_cleanup_enabled:
                  directEntry.offline_cleanup_enabled ?? false,
                offline_cleanup_action:
                  directEntry.offline_cleanup_action === "delete" ||
                  directEntry.offline_cleanup_action === "to_wanted"
                    ? directEntry.offline_cleanup_action
                    : "archive",
                offline_cleanup_delay_hours:
                  directEntry.offline_cleanup_delay_hours ?? 72,
              }
            : null
        }
        isSchedulerEnabled={schedulerStatus?.enabled}
        onClose={() => setEditingDirect(false)}
        onSave={handleDirectFolderSave}
      />
      <DirectDeleteModal
        isOpen={deletingDirect}
        folder={directFolder}
        externalEnabled={externalEnabled}
        onClose={() => setDeletingDirect(false)}
        onConfirm={handleDirectDelete}
      />
      <ExternalPlaylistEditModal
        playlist={editingExternal}
        isOpen={editingExternal !== null}
        isSchedulerEnabled={schedulerStatus?.enabled}
        onClose={() => setEditingExternal(null)}
        onSaved={(updated) => {
          setExternalPlaylists((prev) =>
            prev.map((p) => (p.dir_name === updated.dir_name ? updated : p)),
          );
        }}
      />
      <ExternalPlaylistDeleteModal
        isOpen={deletingExternal !== null}
        dirName={deletingExternal?.dir_name ?? ""}
        allowMutate={deletingExternal?.allow_mutate ?? false}
        onClose={() => setDeletingExternal(null)}
        onConfirm={async (mode: ExternalDeleteMode) => {
          if (!deletingExternal) return false;
          const result = await deleteExternalPlaylist(
            deletingExternal.dir_name,
            mode,
            directFolder,
          );
          if ("error" in result) {
            showErrorToast(t("sync.deleteExternalFailed"), result.error);
            return false;
          }
          showSuccessToast(
            t("sync.deleteExternalDoneTitle"),
            t("sync.deleteExternalDone"),
          );
          void refreshExternal();
          void refreshLibrarySummary();
          return true;
        }}
      />
      <Modal
        isOpen={inputChoice !== null}
        onOpenChange={(open) => {
          if (!open && !isDirecting && !isAdding) setInputChoice(null);
        }}
        placement="center"
      >
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader>{t("sync.inputChoiceTitle")}</ModalHeader>
              <ModalBody className="text-sm">
                <p>
                  {t("sync.inputChoiceBody", {
                    count: inputChoice?.trackCount ?? 0,
                    limit: inputChoice?.limit ?? directDownloadLimit,
                  })}
                </p>
              </ModalBody>
              <ModalFooter>
                <Button
                  variant="light"
                  isDisabled={isDirecting || isAdding}
                  onPress={onClose}
                >
                  {t("sync.cancel")}
                </Button>
                <Button
                  variant="flat"
                  isLoading={isDirecting}
                  isDisabled={isAdding}
                  onPress={() => {
                    void handleChoiceDirect();
                  }}
                >
                  {t("sync.directDownload")}
                </Button>
                <Button
                  color="primary"
                  isLoading={isAdding}
                  isDisabled={isDirecting}
                  onPress={() => {
                    void handleChoiceSubscribe();
                  }}
                >
                  {t("sync.addSubscription")}
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>
      <Modal
        isOpen={elsewhereChoice !== null}
        onOpenChange={(open) => {
          if (!open && !isDirecting) setElsewhereChoice(null);
        }}
        placement="center"
      >
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader>{t("search.elsewhereTitle")}</ModalHeader>
              <ModalBody className="flex flex-col gap-2 text-sm">
                <p>{t("search.elsewhereBody")}</p>
                <Button
                  color="primary"
                  className="w-full justify-start"
                  isLoading={isDirecting}
                  onPress={() => {
                    void handleElsewhereYes();
                  }}
                >
                  {t("search.elsewhereYes")}
                </Button>
                {elsewhereChoice?.locations.map((loc) => (
                  <Button
                    key={loc.expand_key}
                    variant="flat"
                    className="w-full justify-start"
                    isDisabled={isDirecting}
                    onPress={() => handleElsewhereExpand(loc.expand_key)}
                  >
                    {loc.enabled === false
                      ? t("search.elsewherePlaylistDisabled", {
                          name: loc.title,
                        })
                      : loc.title}
                  </Button>
                ))}
              </ModalBody>
              <ModalFooter>
                <Button
                  variant="light"
                  isDisabled={isDirecting}
                  onPress={onClose}
                >
                  {t("sync.cancel")}
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>
      <Modal
        isOpen={textMatchChoice !== null}
        onOpenChange={(open) => {
          if (!open && !isSearching) setTextMatchChoice(null);
        }}
        placement="center"
        scrollBehavior="inside"
      >
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader>{t("search.localMatchTitle")}</ModalHeader>
              <ModalBody className="flex flex-col gap-2 text-sm">
                <p>
                  {t("search.localMatchBody", {
                    query: textMatchChoice?.query ?? "",
                  })}
                </p>
                <Button
                  color="primary"
                  className="w-full justify-start"
                  isLoading={isSearching}
                  onPress={() => {
                    void handleTextMatchOnline();
                  }}
                >
                  {t("search.onlineSearch")}
                </Button>
                {textMatchChoice?.matches.map((match) => (
                  <Button
                    key={match.video_id}
                    variant="flat"
                    className="h-auto min-h-10 w-full justify-start py-2 text-left whitespace-normal"
                    isDisabled={isSearching || isDirecting}
                    onPress={() => {
                      void handleTextMatchSelect(match);
                    }}
                  >
                    <span>
                      {match.artist
                        ? `${match.artist} - ${match.title}`
                        : match.title}
                      {match.in_direct
                        ? ` · ${t("search.localMatchInDirect")}`
                        : ""}
                    </span>
                  </Button>
                ))}
              </ModalBody>
              <ModalFooter>
                <Button
                  variant="light"
                  isDisabled={isSearching || isDirecting}
                  onPress={onClose}
                >
                  {t("sync.cancel")}
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>
      <Modal
        isOpen={directLimitExceeded !== null}
        onOpenChange={(open) => {
          if (!open && !isAdding) setDirectLimitExceeded(null);
        }}
        placement="center"
      >
        <ModalContent>
          {(onClose) => (
            <>
              <ModalHeader>{t("sync.directLimitTitle")}</ModalHeader>
              <ModalBody className="text-sm">
                <p>
                  {t("sync.directLimitBody", {
                    count: directLimitExceeded?.trackCount ?? 0,
                    limit: directLimitExceeded?.limit ?? 0,
                  })}
                </p>
              </ModalBody>
              <ModalFooter>
                <Button variant="light" isDisabled={isAdding} onPress={onClose}>
                  {t("sync.cancelDownload")}
                </Button>
                <Button
                  color="primary"
                  isLoading={isAdding}
                  onPress={() => {
                    void handleSubscribeFromLimit();
                  }}
                >
                  {t("sync.addSubscription")}
                </Button>
              </ModalFooter>
            </>
          )}
        </ModalContent>
      </Modal>
    </>
  );
}
