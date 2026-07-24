import type { Job } from "@/api/jobs";
import { listSubscriptionTracks, type Subscription } from "@/api/subscriptions";
import type { SyncLedgerEntry, SyncTrackItem } from "@/api/sync-ledger";
import { listSyncTracks } from "@/api/sync-ledger";
import { albumCoverUrl, playlistCoverUrl, trackCoverUrl } from "@/api/library";
import { AudioSpectrum } from "@/features/sync/audio-spectrum";
import { LedgerTrackList } from "@/features/sync/ledger-track-list";
import { useLibraryAudio } from "@/features/sync/library-audio";
import type { PlayMode } from "@/features/sync/play-mode";
import { PlaylistStatsLine } from "@/features/sync/playlist-stats-line";
import { SYNC_ACTION_BTN, SYNC_CARD_ACTIONS } from "@/features/sync/track-columns";
import { formatDateTime } from "@/lib/format";
import { isActive, isRunning } from "@/lib/job-status";
import { Button, Card, CardBody, Image, Progress } from "@heroui/react";
import {
  CaptionsIcon,
  ExternalLinkIcon,
  FolderIcon,
  Music2Icon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  Repeat1Icon,
  RepeatIcon,
  ShuffleIcon,
  SkipBackIcon,
  SkipForwardIcon,
  Trash2Icon,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  entry: SyncLedgerEntry;
  subscription?: Subscription | null;
  /** All subscriptions — used to detect shared save folders. */
  subscriptions?: Subscription[];
  activeJob?: Job | null;
  tracksOpen?: boolean;
  onToggleTracks?: () => void;
  onCollapseTracks?: () => void;
  onDirectTrackDeleted?: (entry: SyncLedgerEntry) => void;
  onCancel?: (jobId: string) => void;
  onEdit?: (subscription: Subscription) => void;
  onSync?: (subscriptionId: string) => void;
  onDelete?: (subscription: Subscription) => void;
  onEditDirect?: () => void;
  onDeleteDirect?: () => void;
  onSyncDirect?: () => void;
  schedulerEnabled?: boolean;
};

/** Partition synced files: exclusive + shared + hardlink === synced. */
function ownershipCounts(
  synced: number,
  hardlinkRaw: number,
  folderShared: boolean,
): { exclusive: number; shared: number; hardlink: number } {
  const hard = Math.min(Math.max(0, hardlinkRaw), Math.max(0, synced));
  const real = Math.max(0, synced - hard);
  if (folderShared) {
    return { exclusive: 0, shared: real, hardlink: hard };
  }
  return { exclusive: real, shared: 0, hardlink: hard };
}

const LINE =
  "text-foreground-500 mt-1 truncate whitespace-nowrap font-mono text-xs leading-relaxed";

const ACTION_BTN = SYNC_ACTION_BTN;

function PlayModeIcon({ mode }: { mode: PlayMode }) {
  const cls = "h-4 w-4";
  switch (mode) {
    case "single_loop":
      return <Repeat1Icon className={cls} />;
    case "loop":
      return <RepeatIcon className={cls} />;
    case "shuffle":
      return <ShuffleIcon className={cls} />;
    default:
      return <Music2Icon className={cls} />;
  }
}

function PlaybackTransport({
  folder,
  isPlayingHere,
}: {
  folder: string;
  isPlayingHere: boolean;
}) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const mode = audio.getPlayModeFor(folder);
  const lyricsEnabled = isPlayingHere && audio.lyricsAvailable;
  const lyricsShown = audio.lyricsHeaderVisible;

  return (
    <>
      <Button
        variant="light"
        size="sm"
        isIconOnly
        className={`${ACTION_BTN} hover:text-primary`}
        aria-label={t("sync.prevTrack")}
        onPress={() => audio.playPrevious()}
      >
        <SkipBackIcon className="h-4 w-4" />
      </Button>
      <Button
        variant="light"
        size="sm"
        isIconOnly
        className={`${ACTION_BTN} hover:text-primary`}
        aria-label={isPlayingHere ? t("sync.pauseTrack") : t("sync.playTrack")}
        onPress={() => audio.togglePlaylistFolder(folder)}
      >
        {isPlayingHere ? (
          <PauseIcon className="h-4 w-4" />
        ) : (
          <PlayIcon className="h-4 w-4" />
        )}
      </Button>
      <Button
        variant="light"
        size="sm"
        isIconOnly
        className={`${ACTION_BTN} hover:text-primary`}
        aria-label={t("sync.nextTrack")}
        onPress={() => audio.playNext()}
      >
        <SkipForwardIcon className="h-4 w-4" />
      </Button>
      <Button
        variant="light"
        size="sm"
        isIconOnly
        className={`${ACTION_BTN} hover:text-primary ${lyricsShown ? "text-primary" : ""}`}
        aria-label={t("sync.showLyrics")}
        isDisabled={!lyricsEnabled}
        onPress={() => audio.toggleLyricsHeader()}
      >
        <CaptionsIcon className="h-4 w-4" />
      </Button>
      <Button
        variant="light"
        size="sm"
        isIconOnly
        className={`${ACTION_BTN} hover:text-primary`}
        aria-label={t(`sync.playMode.${mode}`)}
        onPress={() => audio.cyclePlayModeFor(folder)}
      >
        <PlayModeIcon mode={mode} />
      </Button>
    </>
  );
}

function buildOutcomeNote(
  entry: SyncLedgerEntry,
  t: (key: string, opts?: Record<string, unknown>) => string,
): string {
  const parts: string[] = [];
  if (entry.failed_count > 0) {
    parts.push(t("sync.missingFailed", { count: entry.failed_count }));
  }
  if (entry.skipped_region > 0) {
    parts.push(t("sync.missingRegion", { count: entry.skipped_region }));
  }
  if (entry.skipped_ugc > 0) {
    parts.push(t("sync.missingUgc", { count: entry.skipped_ugc }));
  }
  if (entry.skipped_other > 0) {
    parts.push(t("sync.missingOther", { count: entry.skipped_other }));
  }
  return parts.join(" · ");
}

export function LedgerCard({
  entry,
  subscription,
  subscriptions = [],
  activeJob,
  tracksOpen = false,
  onToggleTracks,
  onCollapseTracks,
  onDirectTrackDeleted,
  onCancel,
  onEdit,
  onSync,
  onDelete,
  onEditDirect,
  onDeleteDirect,
  onSyncDirect,
  schedulerEnabled = true,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const rootRef = useRef<HTMLDivElement>(null);
  const running = activeJob ? isRunning(activeJob.status) : false;
  const active = activeJob ? isActive(activeJob.status) : false;
  const isSubscription = entry.kind === "subscription" && !!subscription;
  const isDirect = entry.kind === "direct";

  const title = subscription?.name ?? entry.title;
  const folder = subscription?.save_folder ?? entry.save_folder;
  const thumb = subscription?.thumbnail_url ?? entry.thumbnail_url;

  const folderSubUsers = subscriptions.filter(
    (s) => s.save_folder === folder,
  ).length;
  // Shared: ≥2 subscriptions on this folder, or Direct folder also used by a sub.
  const folderShared = isDirect ? folderSubUsers >= 1 : folderSubUsers >= 2;

  // Live stats combine on-disk files with Direct/subscription list membership.
  const [subStats, setSubStats] = useState<{
    synced: number;
    total: number;
    exclusive: number;
    shared: number;
    hardlink: number;
    offline: number;
    blocked: number;
    missing: number;
  } | null>(null);
  const [directStats, setDirectStats] = useState<{
    local: number;
    cloud: number;
    offline: number;
    blocked: number;
    exclusive: number;
    shared: number;
    hardlink: number;
    missing: number;
  } | null>(null);
  // Direct has no meaningful playlist thumbnail; use its on-disk tracks' art.
  // Subscriptions keep the same list so a dead remote thumb can fall back to
  // embedded / YTM cover from the first present track.
  const [coverTracks, setCoverTracks] = useState<SyncTrackItem[]>([]);

  const loadStats = useCallback(() => {
    if (!folder) {
      setSubStats(null);
      setDirectStats(null);
      setCoverTracks([]);
      return;
    }

    if (isSubscription && subscription?.id) {
      setDirectStats(null);
      void Promise.all([
        listSyncTracks(folder),
        listSubscriptionTracks(subscription.id),
      ]).then(([files, members]) => {
        const present = files.filter(
          (f) => f.exists && f.storage !== "missing",
        );
        const presentIds = new Set(
          present.map((f) => f.video_id).filter(Boolean) as string[],
        );
        const activeMembers = members.filter(
          (m) => m.membership_status === "active",
        );
        const offlineMembers = members.filter(
          (m) => m.membership_status === "offline",
        );
        const blockedMembers = members.filter(
          (m) => m.membership_status === "blocked",
        );
        const hardlink = present.filter((f) => f.storage === "hardlink").length;
        const real = present.length - hardlink;
        // 云端 comes from ledger.total_count (recorded at trusted sync), not
        // from membership math. loadStats only refreshes local/policy counters.
        setSubStats({
          synced: present.length,
          total: 0, // unused — UI reads entry.total_count for 云端
          hardlink,
          exclusive: folderShared ? 0 : real,
          shared: folderShared ? real : 0,
          offline: offlineMembers.length,
          blocked: blockedMembers.length,
          missing: activeMembers.filter(
            (m) => !presentIds.has(m.catalog_video_id || m.video_id),
          ).length,
        });
        setCoverTracks(present);
      });
      return;
    }

    if (isDirect) {
      setSubStats(null);
      void listSyncTracks(folder).then((files) => {
        const present = files.filter(
          (f) => f.exists && f.storage !== "missing",
        );
        const presentIds = new Set(
          present.map((f) => f.video_id).filter(Boolean) as string[],
        );
        const offlineMembers = files.filter(
          (f) => f.membership_status === "offline",
        );
        const blockedMembers = files.filter(
          (f) => f.membership_status === "blocked",
        );
        const activeMembers = files.filter(
          (f) =>
            f.membership_status !== "offline" &&
            f.membership_status !== "blocked",
        );
        const listMembers = files.filter(
          (f) => f.membership_status !== "offline",
        );
        const hardlink = present.filter((f) => f.storage === "hardlink").length;
        const real = present.length - hardlink;
        setDirectStats({
          local: present.length,
          cloud: listMembers.length,
          offline: offlineMembers.length,
          blocked: blockedMembers.length,
          hardlink,
          exclusive: folderShared ? 0 : real,
          shared: folderShared ? real : 0,
          missing: activeMembers.filter(
            (f) => f.video_id && !presentIds.has(f.video_id),
          ).length,
        });
        // Backend returns newest downloads first for Direct.
        setCoverTracks(present);
      });
      return;
    }
    setCoverTracks([]);

    setSubStats(null);
    setDirectStats(null);
  }, [isSubscription, isDirect, subscription?.id, folder, folderShared]);

  useEffect(() => {
    loadStats();
  }, [
    loadStats,
    subscription?.last_synced_at,
    entry.last_synced_at,
    entry.synced_count,
    entry.hardlink_count,
  ]);

  useEffect(() => {
    const onLedgerChanged = () => loadStats();
    window.addEventListener("yubal:ledger-changed", onLedgerChanged);
    return () =>
      window.removeEventListener("yubal:ledger-changed", onLedgerChanged);
  }, [loadStats]);

  const useSub = isSubscription && subStats !== null;
  const useDirect = isDirect && directStats !== null;
  const ownership = useSub
    ? {
        exclusive: subStats.exclusive,
        shared: subStats.shared,
        hardlink: subStats.hardlink,
      }
    : useDirect
      ? {
          exclusive: directStats.exclusive,
          shared: directStats.shared,
          hardlink: directStats.hardlink,
        }
      : ownershipCounts(entry.synced_count, entry.hardlink_count, folderShared);
  const syncedCount = useSub
    ? subStats.synced
    : useDirect
      ? directStats.local
      : entry.synced_count;
  const totalCount = useSub
    ? entry.total_count
    : useDirect
      ? directStats.cloud
      : entry.total_count;
  const offlineCount = useSub
    ? subStats.offline
    : useDirect
      ? directStats.offline
      : 0;
  const blockedCount = useSub
    ? subStats.blocked
    : useDirect
      ? directStats.blocked
      : 0;
  const missing = useSub
    ? subStats.missing
    : useDirect
      ? directStats.missing
      : 0;

  const statsItems =
    isSubscription || isDirect
      ? [
          { label: t("sync.statCloud"), value: totalCount },
          { label: t("sync.statLocal"), value: syncedCount },
          { label: t("sync.statBlocked"), value: blockedCount },
          {
            label: t(
              isSubscription
                ? "sync.statNotInCloudPlaylist"
                : "sync.statIdInvalid",
            ),
            value: offlineCount,
          },
          { label: t("sync.statExclusive"), value: ownership.exclusive },
          { label: t("sync.statShared"), value: ownership.shared },
          { label: t("sync.statHardlink"), value: ownership.hardlink },
        ]
      : [
          { label: t("sync.statCloud"), value: totalCount },
          { label: t("sync.statLocal"), value: syncedCount },
          { label: t("sync.statIdInvalid"), value: offlineCount },
          { label: t("sync.statExclusive"), value: ownership.exclusive },
          { label: t("sync.statShared"), value: ownership.shared },
          { label: t("sync.statHardlink"), value: ownership.hardlink },
        ];

  const outcomeNote = buildOutcomeNote(entry, t);

  const timeLabel = entry.last_synced_at
    ? formatDateTime(entry.last_synced_at)
    : t("time.never");

  let resultLabel = t("sync.resultUnknown");
  let resultDetail = "";
  if (running) {
    resultLabel = t("sync.resultRunning");
  } else if (entry.last_job_status === "completed") {
    resultLabel = t("sync.resultSuccess");
    if (missing > 0 && outcomeNote) {
      resultDetail = outcomeNote;
    }
  } else if (entry.last_job_status === "failed") {
    resultLabel = t("sync.resultFailed");
    resultDetail = outcomeNote || t("sync.resultFailedFallback");
  } else if (entry.last_job_status === "interrupted") {
    resultLabel = t("sync.resultInterrupted");
  }

  const neverRun = !running && !entry.last_synced_at && !entry.last_job_status;
  const historyLine = neverRun
    ? t(isDirect ? "sync.neverDownloaded" : "sync.neverSynced")
    : t(
        isDirect
          ? resultDetail
            ? "sync.historyDownloadWithReason"
            : "sync.historyDownload"
          : resultDetail
            ? "sync.historySyncWithReason"
            : "sync.historySync",
        {
          time: timeLabel,
          result: resultLabel,
          reason: resultDetail,
        },
      );

  let scheduleStatus: string | null = null;
  if (isSubscription) {
    if (running) {
      scheduleStatus = t("sync.statusSyncing");
    } else if (!schedulerEnabled) {
      scheduleStatus = t("sync.statusGlobalStopped");
    } else if (subscription && !subscription.enabled) {
      scheduleStatus = t("sync.statusStopped");
    } else {
      scheduleStatus = t("sync.statusWaiting");
    }
  } else if (isDirect) {
    if (running) {
      scheduleStatus = t("sync.statusSyncing");
    } else if (!schedulerEnabled) {
      scheduleStatus = t("sync.statusGlobalStopped");
    } else if (entry.enabled === false) {
      scheduleStatus = t("sync.statusStopped");
    } else if (entry.enabled) {
      scheduleStatus = t("sync.statusWaiting");
    }
  }
  const historyDisplay = scheduleStatus
    ? `${scheduleStatus} · ${historyLine}`
    : historyLine;

  const canExpand = Boolean(folder && onToggleTracks);
  const isActiveFolder = audio.activeFolder === folder;
  const isPlayingHere = isActiveFolder && audio.playing;
  // A paused playlist returns to its own cover instead of retaining track art.
  const playingTrackKey = isPlayingHere ? audio.key : null;
  const [coverIdx, setCoverIdx] = useState(0);
  // Transport only while this folder is actually playing (hidden when paused).
  const showTransport = Boolean(folder && canExpand && isPlayingHere);

  const statsWithFolder = (
    <PlaylistStatsLine
      items={statsItems}
      trailing={
        (isSubscription || isDirect) && missing > 0 ? (
          <span className="text-warning">
            <span className="text-foreground-400 px-1" aria-hidden>
              ·
            </span>
            {t("sync.missingHint", { count: missing })}
          </span>
        ) : null
      }
    />
  );

  const headline = isPlayingHere ? audio.nowPlayingLabel || title : title;
  const subline = isPlayingHere ? (
    <>
      <span className="text-foreground">{title}</span>
      {" · "}
      {statsWithFolder}
    </>
  ) : (
    statsWithFolder
  );

  const trackPath = useCallback(
    (track: SyncTrackItem) =>
      `${folder}/${track.relative_path}`.replace(/\/+/g, "/"),
    [folder],
  );

  // Ordered cover candidates; on load error we fall through to the next one,
  // and finally to the folder icon — so a dead URL never leaves a blank hole.
  // Idle: playlist sidecar → online playlist thumb (subs) → album cover.jpg →
  // embedded → remote track art. While playing, current-track art is tried first.
  const coverCandidates = useMemo(() => {
    const list: string[] = [];
    const push = (url: string | null | undefined) => {
      if (url && !list.includes(url)) list.push(url);
    };

    const playing = playingTrackKey
      ? coverTracks.find((tk) => trackPath(tk) === playingTrackKey)
      : null;
    const primary = playing ?? coverTracks[0];
    const primaryPath = primary ? trackPath(primary) : null;

    if (playingTrackKey && primaryPath) {
      push(trackCoverUrl(playingTrackKey));
      push(albumCoverUrl(primaryPath));
      push(primary?.cover_url);
    }

    if (folder) push(playlistCoverUrl(folder));
    if (!isDirect) push(thumb);

    if (primaryPath && !playingTrackKey) {
      push(albumCoverUrl(primaryPath));
      push(trackCoverUrl(primaryPath));
      push(primary?.cover_url);
    }
    return list;
  }, [isDirect, playingTrackKey, coverTracks, trackPath, thumb, folder]);

  const coverKey = coverCandidates.join("|");
  useEffect(() => {
    setCoverIdx(0);
  }, [coverKey]);

  const coverSrc = coverCandidates[coverIdx];

  useEffect(() => {
    if (!tracksOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (!root) return;
      const target = event.target;
      if (target instanceof Node && root.contains(target)) return;
      // HeroUI Modal portals to body; treat dialogs as inside the card.
      if (
        target instanceof Element &&
        target.closest('[role="dialog"], [aria-modal="true"]')
      ) {
        return;
      }
      onCollapseTracks?.();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [tracksOpen, onCollapseTracks]);

  return (
    <div ref={rootRef}>
      <Card shadow="sm" className="bg-content1 overflow-hidden">
        <CardBody className="relative flex flex-row items-center gap-3 overflow-hidden p-0">
          {isPlayingHere ? <AudioSpectrum /> : null}
          {isPlayingHere ? (
            <div
              className="pointer-events-none absolute inset-x-0 bottom-0 z-20 h-0.5 overflow-hidden"
              aria-hidden
            >
              <div
                className="bg-primary h-full transition-[width] duration-100 ease-linear"
                style={{ width: `${audio.progress * 100}%` }}
              />
            </div>
          ) : null}
          {isPlayingHere ? (
            <button
              type="button"
              className="absolute inset-x-0 bottom-0 z-30 h-[20%] cursor-pointer border-0 bg-transparent p-0"
              aria-label={t("sync.seekTrack")}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                const rect = e.currentTarget.getBoundingClientRect();
                const ratio = (e.clientX - rect.left) / Math.max(1, rect.width);
                audio.seek(Math.min(1, Math.max(0, ratio)));
              }}
              onPointerDown={(e) => {
                // Block expand/collapse hit target under this strip.
                e.stopPropagation();
              }}
            />
          ) : null}
          {folder ? (
            <button
              type="button"
              className="group focus-visible:ring-primary relative z-20 m-3 h-14 w-14 shrink-0 overflow-hidden rounded-md outline-none focus-visible:ring-2"
              aria-label={
                isPlayingHere ? t("sync.pauseTrack") : t("sync.playTrack")
              }
              onClick={(e) => {
                e.stopPropagation();
                audio.togglePlaylistFolder(folder);
              }}
            >
              {/* Plain img: HeroUI Image wrapper sits above overlays and blocks hover UI */}
              {coverSrc ? (
                <img
                  src={coverSrc}
                  alt=""
                  width={56}
                  height={56}
                  className="absolute inset-0 z-0 h-14 w-14 object-cover transition duration-200 group-hover:scale-[1.03] group-hover:brightness-[0.78]"
                  onError={() => setCoverIdx((i) => i + 1)}
                />
              ) : (
                <div className="bg-default-100 absolute inset-0 z-0 flex items-center justify-center transition duration-200 group-hover:brightness-90">
                  <FolderIcon className="text-foreground-400 h-6 w-6" />
                </div>
              )}
              <span className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-md bg-black/30 opacity-0 transition-opacity duration-150 group-hover:opacity-100">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-white shadow-md">
                  {isPlayingHere ? (
                    <PauseIcon
                      className="h-4 w-4 text-black"
                      fill="currentColor"
                    />
                  ) : (
                    <PlayIcon
                      className="ml-0.5 h-4 w-4 text-black"
                      fill="currentColor"
                    />
                  )}
                </span>
              </span>
            </button>
          ) : (
            <div className="m-3 h-14 w-14 shrink-0 overflow-hidden rounded-md">
              {thumb ? (
                <Image
                  src={thumb}
                  alt=""
                  width={56}
                  height={56}
                  radius="md"
                  className="h-14 w-14 object-cover"
                />
              ) : (
                <div className="bg-default-100 flex h-14 w-14 items-center justify-center rounded-md">
                  <FolderIcon className="text-foreground-400 h-6 w-6" />
                </div>
              )}
            </div>
          )}

          <button
            type="button"
            className={`relative z-10 flex min-w-0 flex-1 items-center gap-3 py-3 pr-0 text-left outline-none ${
              canExpand ? "cursor-pointer" : ""
            }`}
            onClick={() => onToggleTracks?.()}
            disabled={!canExpand}
            aria-expanded={tracksOpen}
            aria-label={t("sync.toggleTrackList")}
          >
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 flex-nowrap items-center gap-2">
                <p className="text-foreground min-w-0 truncate text-sm font-medium">
                  {headline}
                </p>
              </div>

              <p className={LINE}>{subline}</p>

              <p className={LINE}>{historyDisplay}</p>

              {activeJob && active && (
                <Progress
                  size="sm"
                  aria-label={t("sync.progress")}
                  value={activeJob.progress}
                  className="mt-2 max-w-md"
                  color="primary"
                />
              )}
            </div>
          </button>

          <div className={SYNC_CARD_ACTIONS}>
            {activeJob && active && onCancel ? (
              <Button
                variant="light"
                size="sm"
                className="text-foreground-500 hover:text-danger"
                onPress={() => onCancel(activeJob.id)}
              >
                {t("sync.cancel")}
              </Button>
            ) : isSubscription && subscription ? (
              <>
                {showTransport && folder ? (
                  <PlaybackTransport
                    folder={folder}
                    isPlayingHere={isPlayingHere}
                  />
                ) : null}
                <Button
                  as="a"
                  href={subscription.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-primary`}
                  aria-label={t("sync.openInYtm")}
                  title={t("sync.openInYtm")}
                >
                  <ExternalLinkIcon className="h-4 w-4" />
                </Button>
                <Button
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-primary`}
                  aria-label={t("playlists.editFolderAction")}
                  onPress={() => onEdit?.(subscription)}
                >
                  <PencilIcon className="h-4 w-4" />
                </Button>
                <Button
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-primary`}
                  aria-label={t("sync.syncNow")}
                  onPress={() => onSync?.(subscription.id)}
                >
                  <RefreshCwIcon className="h-4 w-4" />
                </Button>
                <Button
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-danger`}
                  aria-label={t("sync.deleteSubscription")}
                  onPress={() => onDelete?.(subscription)}
                >
                  <Trash2Icon className="h-4 w-4" />
                </Button>
              </>
            ) : isDirect ? (
              <>
                {showTransport && folder ? (
                  <PlaybackTransport
                    folder={folder}
                    isPlayingHere={isPlayingHere}
                  />
                ) : null}
                <Button
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-primary`}
                  aria-label={t("sync.editDirectTitle")}
                  onPress={() => onEditDirect?.()}
                >
                  <PencilIcon className="h-4 w-4" />
                </Button>
                <Button
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-primary`}
                  aria-label={t("sync.syncDirect")}
                  onPress={() => onSyncDirect?.()}
                >
                  <RefreshCwIcon className="h-4 w-4" />
                </Button>
                <Button
                  variant="light"
                  size="sm"
                  isIconOnly
                  className={`${ACTION_BTN} hover:text-danger`}
                  aria-label={t("sync.deleteDirectTitle")}
                  onPress={() => onDeleteDirect?.()}
                >
                  <Trash2Icon className="h-4 w-4" />
                </Button>
              </>
            ) : null}
          </div>
        </CardBody>
        <LedgerTrackList
          saveFolder={folder}
          open={tracksOpen}
          canDelete={isDirect || isSubscription}
          subscriptionId={subscription?.id}
          onMembershipChanged={loadStats}
          onDeleted={onDirectTrackDeleted}
        />
      </Card>
    </div>
  );
}
