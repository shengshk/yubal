import type {
  DirectDeleteMode,
  SyncLedgerEntry,
  SyncTrackItem,
} from "@/api/sync-ledger";
import {
  deleteDirectTrack,
  enrichTrack,
  listSyncTracks,
  removeDirectTrackFromList,
  unblockDirectTrack,
} from "@/api/sync-ledger";
import { getSettings, type TrackSortKey } from "@/api/settings";
import {
  disposeSubscriptionTrack,
  deleteSubscriptionTrackFile,
  listSubscriptionTracks,
  downloadSubscriptionTrack,
  unblockSubscriptionTrack,
} from "@/api/subscriptions";
import {
  acceptExternalMatch,
  deleteExternalTrack,
  listExternalPlaylistTracks,
  matchExternalTrack,
  type ExternalMatchCandidate,
  type ExternalTrack,
} from "@/api/external";
import { useLibraryAudio } from "@/features/sync/library-audio";
import { SectionIndexRail } from "@/features/sync/section-index-rail";
import {
  SYNC_ACTION_BTN,
  TRACK_ACTIONS,
  TRACK_INDEX,
  TRACK_INDEX_ICON,
  TRACK_ROW_GRID,
  TrackTextCells,
} from "@/features/sync/track-columns";
import { TrackDeleteModal, type TrackDeleteMode } from "@/features/sync/track-delete-modal";
import { TrackEditModal } from "@/features/sync/track-edit-modal";
import {
  DEFAULT_INDEX_THRESHOLD,
  type IndexLetter,
} from "@/features/sync/track-index";
import {
  assignDisplayNumbers,
  buildOrderedTrackSections,
  lettersInSections,
  orderedSectionDomId,
  resolveJunkKind,
  sortTracksUnified,
  trackIdentity,
  type JunkKind,
} from "@/features/sync/track-list-order";
import { formatArtistTitle } from "@/features/sync/track-label";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import { Button, Modal, ModalBody, ModalContent, ModalFooter, ModalHeader, Spinner } from "@heroui/react";
import {
  ArchiveIcon,
  AudioLinesIcon,
  BanIcon,
  CloudOffIcon,
  DownloadIcon,
  ExternalLinkIcon,
  PencilIcon,
  PlusIcon,
  SparklesIcon,
  Trash2Icon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import { useTranslation } from "react-i18next";

const ADDED_TO_DIRECT_KEY = "yubal:added-to-direct";
const ADDED_TTL_MS = 24 * 60 * 60 * 1000;

function readAddedToDirect(): Record<string, number> {
  try {
    const raw = localStorage.getItem(ADDED_TO_DIRECT_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, number>;
    const now = Date.now();
    const kept: Record<string, number> = {};
    for (const [k, ts] of Object.entries(parsed)) {
      if (typeof ts === "number" && now - ts < ADDED_TTL_MS) kept[k] = ts;
    }
    return kept;
  } catch {
    return {};
  }
}

function markAddedToDirect(id: string) {
  const map = readAddedToDirect();
  map[id] = Date.now();
  localStorage.setItem(ADDED_TO_DIRECT_KEY, JSON.stringify(map));
}

type Props = {
  saveFolder: string;
  open: boolean;
  canDelete?: boolean;
  /** When set, offline membership is fetched to flag removed-upstream tracks. */
  subscriptionId?: string | null;
  onMembershipChanged?: () => void;
  onDeleted?: (entry: SyncLedgerEntry) => void;
  /**
   * "external" reads from the External-library scan instead of the sync
   * ledger: unmatched files surface as tier "raw" with a match action.
   */
  mode?: "sync" | "external";
  externalDirName?: string;
  /** Playlist-level allow_mutate — gates tag edit / raw delete / tag scrape. */
  allowMutate?: boolean;
  /** Playlist-level show_raw — hides unmatched rows when false. */
  showRaw?: boolean;
  /** Playlist-level show_junk — junk is a subset of unmatched. */
  showJunk?: boolean;
  /** Called after a match action completes, so the parent can refresh counts. */
  onMatched?: () => void;
};

const ACTION_BTN = SYNC_ACTION_BTN;

/** Playlist folder id vs on-disk stream path (External uses Organized/… or Raw/…). */
function trackKey(saveFolder: string, relativePath: string): string {
  return `${saveFolder}/${relativePath}`.replace(/\/+/g, "/");
}

function audioPath(
  saveFolder: string,
  relativePath: string,
  external: boolean,
): string {
  if (external) return relativePath.replace(/^\/+/, "");
  return trackKey(saveFolder, relativePath);
}

/** External quality tier for row-action matrix. */
export type ExternalQualityTier =
  | "junk_rw"
  | "junk_ro"
  | "raw"
  | "complete"
  | "premium";

function externalQualityTier(
  track: SyncTrackItem,
  allowMutate: boolean,
): ExternalQualityTier {
  if (track.tier === "premium") return "premium";
  const isUnmatched = track.tier === "raw" || !track.video_id;
  if (isUnmatched) {
    const kind = resolveJunkKind(track, allowMutate);
    if (kind === "rw") return "junk_rw";
    if (kind === "ro") return "junk_ro";
    return "raw";
  }
  return "complete";
}

/** Maps an External-library scan row onto the shared SyncTrackItem shape. */
function externalTrackToItem(track: ExternalTrack, index: number): SyncTrackItem {
  const matched =
    track.match_status === "matched" &&
    Boolean(track.video_id) &&
    track.is_raw !== true;
  const junkKind =
    track.junk_kind === "rw" || track.junk_kind === "ro"
      ? track.junk_kind
      : null;
  return {
    index,
    title: track.title,
    artist: track.artist,
    album_artist: track.album_artist ?? null,
    album: track.album ?? null,
    exists: track.exists ?? true,
    storage: "real",
    relative_path: track.rel_path,
    video_id: matched ? track.video_id ?? null : null,
    tier: matched ? track.tier ?? "complete" : "raw",
    cover_source: track.cover_source ?? null,
    cover_url: track.cover_url ?? null,
    year: track.year ?? null,
    track_number: track.track_number ?? null,
    tags_complete:
      track.tags_complete ??
      Boolean(
        track.title?.trim() && track.artist?.trim() && track.album?.trim(),
      ),
    is_junk: Boolean(track.is_junk) || junkKind != null,
    junk_kind: junkKind,
    in_direct: Boolean(track.in_direct),
  };
}

function TrackRow({
  track,
  saveFolder,
  canDelete,
  offline = false,
  offlineKind = "id_invalid",
  blocked = false,
  allowDeleteWhenMissing = false,
  displayIndex,
  busy = false,
  playable = true,
  mutable = true,
  external = false,
  qualityTier = null,
  onDeleteRequest,
  onEditRequest,
  onEnrichRequest,
  onDispose,
  onMatchRequest,
  onUnblockRequest,
  onDownloadRequest,
  onAddToDirect,
  addedToDirect = false,
}: {
  track: SyncTrackItem;
  saveFolder: string;
  canDelete: boolean;
  offline?: boolean;
  /** Subscription removals vs dead cloud ID. */
  offlineKind?: "id_invalid" | "not_in_playlist";
  blocked?: boolean;
  allowDeleteWhenMissing?: boolean;
  displayIndex: string;
  busy?: boolean;
  playable?: boolean;
  mutable?: boolean;
  external?: boolean;
  /** External playlist quality tier; null = non-external / use legacy tier. */
  qualityTier?: ExternalQualityTier | null;
  onDeleteRequest: (track: SyncTrackItem) => void;
  onEditRequest: (track: SyncTrackItem) => void;
  onEnrichRequest: (track: SyncTrackItem) => void;
  onDispose?: (track: SyncTrackItem, action: "archive" | "delete") => void;
  onMatchRequest?: (track: SyncTrackItem) => void;
  onUnblockRequest?: (track: SyncTrackItem) => void;
  onDownloadRequest?: (track: SyncTrackItem) => void;
  onAddToDirect?: (track: SyncTrackItem) => void;
  addedToDirect?: boolean;
}) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const isRaw = track.tier === "raw";
  const missing = !isRaw && (!track.exists || track.storage === "missing");
  const filePath = audioPath(saveFolder, track.relative_path, external);
  const key = filePath;
  const isCurrent = playable && audio.key === key;
  const isPlaying = isCurrent && audio.playing;
  const offlineBadgeLabel =
    offlineKind === "not_in_playlist"
      ? t("sync.offlineBadgeNotInPlaylist")
      : t("sync.offlineBadgeIdInvalid");
  const dimmed = missing || offline || blocked;
  const tier = qualityTier;

  const junkKind: JunkKind | null =
    tier === "junk_rw"
      ? "rw"
      : tier === "junk_ro"
        ? "ro"
        : null;
  const isJunk = junkKind != null;
  const isReadonlyJunk = junkKind === "ro";
  const isWritableJunk = junkKind === "rw";
  const isPremium = tier === "premium" || track.tier === "premium";
  const isUnmatched = Boolean(
    isJunk || tier === "raw" || (!tier && isRaw),
  );

  const showMatchOrEnrich = !blocked && !missing;
  const showUnmatchedMatch = showMatchOrEnrich && isUnmatched;
  const showMatchedEnrich =
    showMatchOrEnrich && !isUnmatched && Boolean(track.video_id);

  const enrichButtonClass = isPremium
    ? "!text-success-400/80 data-[disabled=true]:opacity-100"
    : track.tier === "draft"
      ? "!text-warning-400/90 hover:!text-warning-500"
      : "hover:text-primary";
  const enrichLabel = isPremium
    ? t("sync.tierOptimal")
    : track.tier === "draft"
      ? t("sync.fillCover")
      : track.has_synced_lyrics
        ? t("sync.upgradeCover")
        : t("sync.upgradeLyrics");

  // Edit: writable junk can edit tags; readonly junk is assets-only / locked tags.
  // Readonly External opens assets-only (cover/lyrics); mutable + unmatched
  // still needs a YTM id before tag edits.
  const showEdit =
    !missing &&
    !blocked &&
    (isJunk || Boolean(track.video_id) || (external && isUnmatched));
  const editDisabled = busy || isReadonlyJunk;
  const editNeedsMatch = Boolean(
    external && mutable && isUnmatched && !track.video_id && !isJunk,
  );
  // Readonly junk: row match is allowed (index-only scrape then match),
  // same path as playlist sync “junk scrape & match” for one track.
  const matchDisabled = busy;

  const label = formatArtistTitle(track.artist, track.title);

  const onPlayAreaClick = (e: ReactMouseEvent<HTMLElement>) => {
    if (!playable || missing || blocked) return;
    if (!isCurrent) {
      audio.play(key, filePath, saveFolder);
      return;
    }
    const row = e.currentTarget.closest("li");
    const rect = row?.getBoundingClientRect();
    if (!rect) return;
    const ratio = (e.clientX - rect.left) / Math.max(1, rect.width);
    audio.seek(ratio);
  };

  const rowInert = !playable || missing || blocked;

  const handleEditPress = () => {
    if (editDisabled) return;
    if (editNeedsMatch) {
      showErrorToast(t("sync.editTrackTags"), t("sync.editAfterMatch"));
      return;
    }
    onEditRequest(track);
  };

  return (
    <li className="relative h-8 overflow-hidden">
      <div
        className={`${TRACK_ROW_GRID} ${
          dimmed ? "text-foreground-400" : "text-foreground"
        }`}
      >
        {/* Play/seek hit target: covers index + text, not the action column. */}
        {!rowInert ? (
          <button
            type="button"
            className="absolute inset-y-0 left-0 right-[10.5rem] z-0 cursor-pointer border-0 bg-transparent p-0"
            aria-label={
              isCurrent
                ? t("sync.seekTrack")
                : `${t("sync.playTrack")}: ${label}`
            }
            title={label}
            onClick={onPlayAreaClick}
          />
        ) : null}

        <span className={`${TRACK_INDEX} pointer-events-none relative z-10`}>
          <span className={TRACK_INDEX_ICON} aria-hidden>
            {isPlaying ? (
              <AudioLinesIcon className="text-primary h-3 w-3" />
            ) : null}
          </span>
          <span className="min-w-[1.25rem] text-right">{displayIndex}</span>
        </span>
        <TrackTextCells
          title={track.title}
          artist={track.artist}
          album={track.album}
          albumArtist={track.album_artist}
          passThroughClicks
        />

        <div className={TRACK_ACTIONS}>
          {missing && !offline && !blocked && onDownloadRequest ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isLoading={busy}
              isDisabled={busy || !track.video_id}
              className={`${ACTION_BTN} text-warning hover:text-warning`}
              aria-label={t("sync.trackDownload")}
              title={t("sync.trackDownload")}
              onPress={() => onDownloadRequest(track)}
            >
              <DownloadIcon className="h-3.5 w-3.5" />
            </Button>
          ) : null}
          {showMatchedEnrich && onAddToDirect ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={busy || addedToDirect}
              className={`${ACTION_BTN} ${
                addedToDirect
                  ? "!text-success-400"
                  : "hover:text-primary"
              }`}
              aria-label={
                addedToDirect
                  ? t("sync.addToDirectDone")
                  : t("sync.addToDirect")
              }
              title={
                addedToDirect
                  ? t("sync.addToDirectDone")
                  : t("sync.addToDirect")
              }
              onPress={() => {
                if (addedToDirect) return;
                onAddToDirect(track);
              }}
            >
              <PlusIcon className="h-3.5 w-3.5" />
            </Button>
          ) : null}
          {track.video_id ? (
            <Button
              as="a"
              href={`https://music.youtube.com/watch?v=${encodeURIComponent(track.video_id)}`}
              target="_blank"
              rel="noopener noreferrer"
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.openInYtm")}
              title={t("sync.openInYtm")}
            >
              <ExternalLinkIcon className="h-3.5 w-3.5" />
            </Button>
          ) : null}

          {showEdit ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={editDisabled}
              className={`${ACTION_BTN} ${
                editDisabled ? "opacity-40" : "hover:text-primary"
              }`}
              aria-label={
                mutable ? t("sync.editTrackTags") : t("sync.editTrackAssets")
              }
              title={
                isReadonlyJunk
                  ? t("sync.editJunkRoHint")
                  : isWritableJunk
                    ? t("sync.editJunkRwHint")
                    : editNeedsMatch
                      ? t("sync.editAfterMatch")
                      : mutable
                        ? t("sync.editTrackTags")
                        : t("sync.editTrackAssetsHint")
              }
              onPress={handleEditPress}
            >
              <PencilIcon className="h-3.5 w-3.5" />
            </Button>
          ) : null}

          {showUnmatchedMatch ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={matchDisabled}
              className={`${ACTION_BTN} !text-danger hover:!text-danger-600`}
              aria-label={
                isReadonlyJunk
                  ? t("sync.matchJunkRoTitle")
                  : isWritableJunk
                    ? t("sync.matchJunkRwTitle")
                    : t("sync.matchTrack")
              }
              title={
                isReadonlyJunk
                  ? t("sync.matchJunkRoHint")
                  : isWritableJunk
                    ? t("sync.matchJunkRwHint")
                    : t("sync.matchTrack")
              }
              onPress={() => {
                if (!matchDisabled) onMatchRequest?.(track);
              }}
            >
              <SparklesIcon className="h-3.5 w-3.5" />
            </Button>
          ) : null}

          {showMatchedEnrich ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={busy || isPremium}
              className={`${ACTION_BTN} ${enrichButtonClass}`}
              aria-label={enrichLabel}
              title={enrichLabel}
              onPress={() => onEnrichRequest(track)}
            >
              <SparklesIcon className="h-3.5 w-3.5" />
            </Button>
          ) : null}

          {blocked ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={busy}
              className={`${ACTION_BTN} !text-danger hover:!text-danger-600`}
              aria-label={t("sync.blockedTrackActions")}
              title={t("sync.blockedTrackActions")}
              onPress={() => onUnblockRequest?.(track)}
            >
              <BanIcon className="h-3.5 w-3.5" />
            </Button>
          ) : offline && onDispose ? (
            <>
              <span
                className="text-warning flex h-7 w-7 items-center justify-center"
                aria-label={offlineBadgeLabel}
              >
                <CloudOffIcon className="h-3.5 w-3.5" />
              </span>
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled={busy}
                className={`${ACTION_BTN} hover:text-primary`}
                aria-label={t("sync.offlineActionArchive")}
                onPress={() => onDispose(track, "archive")}
              >
                <ArchiveIcon className="h-3.5 w-3.5" />
              </Button>
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled={busy}
                className={`${ACTION_BTN} hover:text-danger`}
                aria-label={t("sync.offlineActionDelete")}
                onPress={() => onDispose(track, "delete")}
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </Button>
            </>
          ) : offline && !onDispose && canDelete ? (
            <>
              <span
                className="text-warning flex h-7 w-7 items-center justify-center"
                aria-label={offlineBadgeLabel}
              >
                <CloudOffIcon className="h-3.5 w-3.5" />
              </span>
              <Button
                variant="light"
                size="sm"
                isIconOnly
                className={`${ACTION_BTN} hover:text-danger`}
                isDisabled={missing && !allowDeleteWhenMissing}
                aria-label={t("sync.deleteTrack")}
                onPress={() => onDeleteRequest(track)}
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </Button>
            </>
          ) : canDelete ? (
            <Button
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-danger`}
              isDisabled={missing && !allowDeleteWhenMissing}
              aria-label={t("sync.deleteTrack")}
              onPress={() => onDeleteRequest(track)}
            >
              <Trash2Icon className="h-3.5 w-3.5" />
            </Button>
          ) : null}
        </div>
      </div>
    </li>
  );
}

export function LedgerTrackList({
  saveFolder,
  open,
  canDelete = false,
  subscriptionId,
  onMembershipChanged,
  onDeleted,
  mode = "sync",
  externalDirName,
  allowMutate = true,
  showRaw = true,
  showJunk = true,
  onMatched,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const isExternal = mode === "external";
  const [tracks, setTracks] = useState<SyncTrackItem[] | null>(null);
  const [offlineIds, setOfflineIds] = useState<Set<string>>(new Set());
  const [blockedIds, setBlockedIds] = useState<Set<string>>(new Set());
  const [offlineMembershipIds, setOfflineMembershipIds] = useState<
    Map<string, string>
  >(new Map());
  const [offlineExtra, setOfflineExtra] = useState<SyncTrackItem[]>([]);
  const [busyVideoId, setBusyVideoId] = useState<string | null>(null);
  const [matchingRelPath, setMatchingRelPath] = useState<string | null>(null);
  const [matchPick, setMatchPick] = useState<{
    track: SyncTrackItem;
    candidates: ExternalMatchCandidate[];
  } | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const [acceptingMatch, setAcceptingMatch] = useState(false);
  const [loading, setLoading] = useState(false);
  const [externalEnabled, setExternalEnabled] = useState(false);
  const [addedMap, setAddedMap] = useState<Record<string, number>>(() =>
    readAddedToDirect(),
  );
  const [pendingDelete, setPendingDelete] = useState<SyncTrackItem | null>(
    null,
  );
  const [pendingUnblock, setPendingUnblock] = useState<SyncTrackItem | null>(
    null,
  );
  const [pendingEdit, setPendingEdit] = useState<SyncTrackItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const reloadToken = useRef(0);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [indexThreshold, setIndexThreshold] = useState(DEFAULT_INDEX_THRESHOLD);
  const [trackSortKey, setTrackSortKey] = useState<TrackSortKey>("title");

  const reload = useCallback(() => {
    if (!open) return;

    if (isExternal) {
      if (!externalDirName) return;
      const token = ++reloadToken.current;
      setLoading(true);
      void listExternalPlaylistTracks(externalDirName).then((items) => {
        if (token !== reloadToken.current) return;
        const mapped = items
          .map((item, i) => externalTrackToItem(item, i))
          .filter((item) => showRaw || item.tier !== "raw")
          .filter((item) => showJunk || !item.is_junk);
        setTracks(mapped);
        setOfflineIds(new Set());
        setBlockedIds(new Set());
        setOfflineMembershipIds(new Map());
        setOfflineExtra([]);
        setLoading(false);
      });
      return;
    }

    if (!saveFolder) return;
    const token = ++reloadToken.current;
    setLoading(true);
    const filesPromise = listSyncTracks(saveFolder);
    const membersPromise = subscriptionId
      ? listSubscriptionTracks(subscriptionId)
      : Promise.resolve([]);
    void Promise.all([filesPromise, membersPromise]).then(
      ([items, members]) => {
        if (token !== reloadToken.current) return;
        if (subscriptionId) {
          const offline = members.filter(
            (member) => member.membership_status === "offline",
          );
          const blocked = members.filter(
            (member) => member.membership_status === "blocked",
          );
          const active = members.filter(
            (member) => member.membership_status === "active",
          );
          const offlineById = new Set(
            offline.map((m) => m.catalog_video_id || m.video_id),
          );
          const blockedById = new Set(
            blocked.map((m) => m.catalog_video_id || m.video_id),
          );
          const membershipIds = new Map(
            members.map((member) => [
              member.catalog_video_id || member.video_id,
              member.video_id,
            ]),
          );
          // Catalog/disk rows (including storage=missing placeholders).
          const presentIds = new Set(
            items.map((it) => it.video_id).filter(Boolean) as string[],
          );
          // Every membership without a folder row must appear: active (缺),
          // offline, blocked. Previously only offline/blocked were synthesized,
          // so cloud=N missing=N with an empty list after catalog wipe.
          const logicalExtras = [...active, ...offline, ...blocked]
            .filter((m) => {
              const id = m.catalog_video_id || m.video_id;
              return id && !presentIds.has(id);
            })
            .map((m, i) => ({
              index: items.length + i + 1,
              title: m.title,
              artist: m.artist,
              album_artist: m.album_artist,
              display_label: formatArtistTitle(m.artist, m.title),
              exists: false,
              storage: "missing" as const,
              relative_path: "",
              video_id: m.catalog_video_id || m.video_id,
              membership_status: m.membership_status,
            }));
          setTracks(items);
          setOfflineIds(offlineById);
          setBlockedIds(blockedById);
          setOfflineMembershipIds(membershipIds);
          setOfflineExtra(logicalExtras);
        } else {
          const offlineById = new Set(
            items
              .filter((it) => it.membership_status === "offline" && it.video_id)
              .map((it) => it.video_id as string),
          );
          const blockedById = new Set(
            items
              .filter((it) => it.membership_status === "blocked" && it.video_id)
              .map((it) => it.video_id as string),
          );
          setTracks(items);
          setOfflineIds(offlineById);
          setBlockedIds(blockedById);
          setOfflineMembershipIds(new Map());
          setOfflineExtra([]);
        }
        setLoading(false);
      },
    );
  }, [open, saveFolder, subscriptionId, isExternal, externalDirName, showRaw, showJunk]);

  useEffect(() => {
    if (!open) {
      setTracks(null);
      setOfflineIds(new Set());
      setBlockedIds(new Set());
      setOfflineMembershipIds(new Map());
      setOfflineExtra([]);
      return;
    }
    reload();
  }, [open, reload]);

  useEffect(() => {
    if (!open) return;
    const onLedgerChanged = () => {
      reload();
    };
    window.addEventListener("yubal:ledger-changed", onLedgerChanged);
    return () => {
      window.removeEventListener("yubal:ledger-changed", onLedgerChanged);
    };
  }, [open, reload]);

  useEffect(() => {
    const loadSettings = () => {
      void getSettings().then((settings) => {
        if (!settings) return;
        setIndexThreshold(settings.index_threshold ?? DEFAULT_INDEX_THRESHOLD);
        setTrackSortKey(settings.track_sort_key ?? "title");
        setExternalEnabled(Boolean(settings.external_library_enabled));
      });
    };
    loadSettings();
    window.addEventListener("yubal:settings-changed", loadSettings);
    return () => {
      window.removeEventListener("yubal:settings-changed", loadSettings);
    };
  }, []);

  const combinedTracks = useMemo(() => {
    if (!tracks) return null;
    return [...tracks, ...offlineExtra];
  }, [tracks, offlineExtra]);

  const orderCtx = useMemo(
    () => ({
      external: isExternal,
      allowMutate,
      offlineIds,
      blockedIds,
    }),
    [isExternal, allowMutate, offlineIds, blockedIds],
  );

  const orderedTracks = useMemo(() => {
    if (!combinedTracks) return null;
    return sortTracksUnified(combinedTracks, trackSortKey, orderCtx);
  }, [combinedTracks, trackSortKey, orderCtx]);

  const indexedMode = Boolean(
    combinedTracks && combinedTracks.length >= indexThreshold,
  );

  const sections = useMemo(() => {
    if (!orderedTracks || !indexedMode) return null;
    return buildOrderedTrackSections(orderedTracks, trackSortKey);
  }, [orderedTracks, indexedMode, trackSortKey]);

  /** Current track in this folder (playing or paused) — pinned above the list. */
  const pinnedTrack = useMemo(() => {
    if (!combinedTracks || audio.activeFolder !== saveFolder || !audio.key) {
      return null;
    }
    return (
      combinedTracks.find(
        (track) =>
          audioPath(saveFolder, track.relative_path, isExternal) === audio.key,
      ) ?? null
    );
  }, [combinedTracks, audio.activeFolder, audio.key, saveFolder, isExternal]);

  const pinnedKey = pinnedTrack
    ? audioPath(saveFolder, pinnedTrack.relative_path, isExternal)
    : null;

  /** Flat display order: playing pinned, then unified bucket sort. */
  const displayTracks = useMemo(() => {
    if (!orderedTracks) return null;
    if (!pinnedKey) return orderedTracks;
    return orderedTracks.filter(
      (track) =>
        audioPath(saveFolder, track.relative_path, isExternal) !== pinnedKey,
    );
  }, [orderedTracks, pinnedKey, saveFolder, isExternal]);

  const listSections = useMemo(() => {
    if (!sections) return null;
    if (!pinnedKey) return sections;
    return sections
      .map((section) => ({
        ...section,
        tracks: section.tracks.filter(
          (track) =>
            audioPath(saveFolder, track.relative_path, isExternal) !==
            pinnedKey,
        ),
      }))
      .filter((section) => section.tracks.length > 0);
  }, [sections, pinnedKey, saveFolder, isExternal]);

  const sectionLetters = useMemo(
    () => (listSections ? lettersInSections(listSections) : []),
    [listSections],
  );

  useEffect(() => {
    if (!orderedTracks) return;
    const rest = pinnedKey
      ? orderedTracks.filter(
          (track) =>
            audioPath(saveFolder, track.relative_path, isExternal) !==
            pinnedKey,
        )
      : orderedTracks;
    const ordered = pinnedTrack ? [pinnedTrack, ...rest] : rest;
    audio.registerPlaylist(
      saveFolder,
      ordered
        .filter((t) => t.exists && t.storage !== "missing")
        .map((t) => {
          const path = audioPath(saveFolder, t.relative_path, isExternal);
          return {
            key: path,
            path,
            label: formatArtistTitle(t.artist, t.title),
          };
        }),
    );
  }, [
    audio,
    saveFolder,
    orderedTracks,
    pinnedTrack,
    pinnedKey,
    isExternal,
  ]);

  const jumpToLetter = useCallback(
    (letter: IndexLetter) => {
      const root = viewportRef.current;
      if (!root) return;
      // First occurrence of this letter in the bucket-ordered section list.
      const el = root.querySelector(
        `#${CSS.escape(orderedSectionDomId(saveFolder, letter, 0))}`,
      );
      if (!(el instanceof HTMLElement)) return;
      // Scroll only the list viewport — never the page.
      const delta =
        el.getBoundingClientRect().top - root.getBoundingClientRect().top;
      root.scrollTo({ top: root.scrollTop + delta, behavior: "smooth" });
    },
    [saveFolder],
  );

  const sectionsWithIds = useMemo(() => {
    if (!listSections) return null;
    const occ = new Map<IndexLetter, number>();
    return listSections.map((section) => {
      const n = occ.get(section.letter) ?? 0;
      occ.set(section.letter, n + 1);
      return {
        ...section,
        domId: orderedSectionDomId(saveFolder, section.letter, n),
      };
    });
  }, [listSections, saveFolder]);

  const [inViewLetters, setInViewLetters] = useState<IndexLetter[]>([]);

  useEffect(() => {
    const root = viewportRef.current;
    if (!root || !sectionsWithIds || sectionsWithIds.length === 0) {
      setInViewLetters([]);
      return;
    }

    const update = () => {
      const rootRect = root.getBoundingClientRect();
      const next: IndexLetter[] = [];
      for (const section of sectionsWithIds) {
        const header = root.querySelector(`#${CSS.escape(section.domId)}`);
        const block = header?.closest("li");
        const target = block instanceof HTMLElement ? block : header;
        if (!(target instanceof HTMLElement)) continue;
        const rect = target.getBoundingClientRect();
        if (rect.bottom > rootRect.top && rect.top < rootRect.bottom) {
          next.push(section.letter);
        }
      }
      setInViewLetters((prev) => {
        if (
          prev.length === next.length &&
          prev.every((letter, i) => letter === next[i])
        ) {
          return prev;
        }
        return next;
      });
    };

    update();
    root.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(root);
    return () => {
      root.removeEventListener("scroll", update);
      observer.disconnect();
    };
  }, [sectionsWithIds]);

  const displayNumbers = useMemo(() => {
    if (!combinedTracks) return new Map<string, string>();
    return assignDisplayNumbers(combinedTracks, trackSortKey, orderCtx);
  }, [combinedTracks, trackSortKey, orderCtx]);

  const disposeOffline = async (
    track: SyncTrackItem,
    action: "archive" | "delete",
  ) => {
    if (!subscriptionId || !track.video_id || busyVideoId) return;
    setBusyVideoId(track.video_id);
    const membershipVideoId =
      offlineMembershipIds.get(track.video_id) ?? track.video_id;
    const ok = await disposeSubscriptionTrack(
      subscriptionId,
      membershipVideoId,
      action,
    );
    setBusyVideoId(null);
    if (ok) {
      reload();
      onMembershipChanged?.();
      // Archived tracks move into the Direct library; refresh all ledgers.
      window.dispatchEvent(new Event("yubal:ledger-changed"));
    }
  };

  const handleEnrichTrack = async (track: SyncTrackItem) => {
    if (!track.video_id || busyVideoId || track.tier === "premium") return;
    setBusyVideoId(track.video_id);
    const summary = await enrichTrack(track.video_id);
    setBusyVideoId(null);
    if (!summary) {
      showErrorToast(t("sync.enrichFailedTitle"), t("sync.enrichFailedBody"));
      return;
    }
    if (summary.already_running) {
      showErrorToast(t("sync.enrichBusyTitle"), t("sync.enrichBusyBody"));
      return;
    }
    if (summary.failed > 0) {
      showErrorToast(t("sync.enrichFailedTitle"), t("sync.enrichTrackFailed"));
    } else if (summary.upgraded > 0) {
      showSuccessToast(
        t("sync.enrichTrackDoneTitle"),
        t("sync.enrichTrackUpgraded"),
      );
    } else {
      showSuccessToast(
        t("sync.enrichTrackDoneTitle"),
        t("sync.enrichTrackUnchanged"),
      );
    }
    reload();
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const handleMatchTrack = async (track: SyncTrackItem) => {
    if (!isExternal || matchingRelPath || acceptingMatch) return;
    setMatchingRelPath(track.relative_path);
    const result = await matchExternalTrack(track.relative_path);
    setMatchingRelPath(null);
    if ("error" in result) {
      showErrorToast(t("sync.matchFailedTitle"), result.error);
      return;
    }
    if (result.matched) {
      showSuccessToast(t("sync.matchDoneTitle"), t("sync.matchTrackMatched"));
      reload();
      onMatched?.();
      return;
    }
    const candidates = result.candidates ?? [];
    if (candidates.length > 0) {
      setSelectedCandidateId(candidates[0]?.video_id ?? null);
      setMatchPick({ track, candidates });
      return;
    }
    showSuccessToast(
      t("sync.matchDoneTitle"),
      t("sync.matchTrackNoCandidate"),
    );
    reload();
    onMatched?.();
  };

  const handleAcceptCandidate = async () => {
    if (!matchPick || !selectedCandidateId || acceptingMatch) return;
    const selected = matchPick.candidates.find(
      (c) => c.video_id === selectedCandidateId,
    );
    setAcceptingMatch(true);
    const result = await acceptExternalMatch(
      matchPick.track.relative_path,
      selectedCandidateId,
      selected?.score,
    );
    setAcceptingMatch(false);
    if ("error" in result) {
      showErrorToast(t("sync.matchFailedTitle"), result.error);
      return;
    }
    setMatchPick(null);
    setSelectedCandidateId(null);
    showSuccessToast(t("sync.matchDoneTitle"), t("sync.matchTrackMatched"));
    reload();
    onMatched?.();
  };

  const handleTagsSaved = (result: {
    locations: Array<{
      save_folder: string;
      old_relative_path: string;
      new_relative_path: string;
    }>;
  }) => {
    const moved = result.locations.find((loc) => loc.save_folder === saveFolder);
    if (
      moved &&
      audio.key ===
        audioPath(saveFolder, moved.old_relative_path, isExternal)
    ) {
      if (moved.old_relative_path !== moved.new_relative_path) {
        audio.pause();
      }
    }
    reload();
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const handleDeleted = (entry: SyncLedgerEntry) => {
    onDeleted?.(entry);
    reload();
  };

  const confirmDelete = async (mode?: TrackDeleteMode) => {
    if (!pendingDelete || deleting) return;
    setDeleting(true);
    let ok = false;
    if (isExternal && externalDirName) {
      const isRaw = pendingDelete.tier === "raw";
      const deleteMode = isRaw
        ? "delete_raw"
        : mode === "add_to_direct"
          ? "add_to_direct"
          : mode === "clear_match" || mode === "wipe_list"
            ? "clear_match"
            : "keep_match";
      const result = await deleteExternalTrack(
        externalDirName,
        pendingDelete.relative_path,
        deleteMode,
      );
      if ("error" in result) {
        showErrorToast(t("sync.deleteTrack"), result.error);
      } else if (result.ok) {
        if (deleteMode === "add_to_direct") {
          const id =
            pendingDelete.video_id || pendingDelete.relative_path;
          markAddedToDirect(id);
          setAddedMap(readAddedToDirect());
        }
        const key = audioPath(
          saveFolder,
          pendingDelete.relative_path,
          true,
        );
        if (audio.key === key) audio.pause();
        setPendingDelete(null);
        reload();
        onMatched?.();
        window.dispatchEvent(new Event("yubal:ledger-changed"));
        ok = true;
      }
    } else if (subscriptionId && pendingDelete.video_id) {
      const membershipVideoId =
        offlineMembershipIds.get(pendingDelete.video_id) ??
        pendingDelete.video_id;
      ok = await deleteSubscriptionTrackFile(
        subscriptionId,
        membershipVideoId,
        mode === "block",
      );
      if (ok) {
        setPendingDelete(null);
        reload();
        onMembershipChanged?.();
        window.dispatchEvent(new Event("yubal:ledger-changed"));
      }
    } else {
      const entry = await deleteDirectTrack(
        pendingDelete.relative_path,
        (mode as DirectDeleteMode) ?? "keep_list",
      );
      if (entry) {
        const key = trackKey(saveFolder, pendingDelete.relative_path);
        if (audio.key === key) audio.pause();
        setPendingDelete(null);
        handleDeleted(entry);
        window.dispatchEvent(new Event("yubal:ledger-changed"));
        ok = true;
      }
    }
    setDeleting(false);
  };

  const confirmUnblock = async () => {
    if (!pendingUnblock?.video_id || deleting) return;
    setDeleting(true);
    let ok = false;
    if (subscriptionId) {
      ok = await unblockSubscriptionTrack(
        subscriptionId,
        pendingUnblock.video_id,
      );
    } else {
      const entry = await unblockDirectTrack(pendingUnblock.video_id);
      ok = Boolean(entry);
      if (entry) handleDeleted(entry);
    }
    setDeleting(false);
    if (ok) {
      setPendingUnblock(null);
      reload();
      onMembershipChanged?.();
      window.dispatchEvent(new Event("yubal:ledger-changed"));
    }
  };

  const handleDownloadMissing = async (track: SyncTrackItem) => {
    if (!subscriptionId || !track.video_id || busyVideoId) return;
    const membershipVideoId =
      offlineMembershipIds.get(track.video_id) ?? track.video_id;
    setBusyVideoId(track.video_id);
    const result = await downloadSubscriptionTrack(
      subscriptionId,
      membershipVideoId,
    );
    setBusyVideoId(null);
    if (!result.success) {
      showErrorToast(result.error || t("sync.trackDownloadFailed"));
      return;
    }
    showSuccessToast(t("sync.trackDownloadQueued"));
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const confirmRemoveBlocked = async () => {
    if (!pendingUnblock?.video_id || deleting) return;
    // Subscriptions: removing blocked rows conflicts with sync — not offered.
    if (subscriptionId) return;
    setDeleting(true);
    const entry = await removeDirectTrackFromList(pendingUnblock.video_id);
    setDeleting(false);
    if (entry) {
      setPendingUnblock(null);
      handleDeleted(entry);
      reload();
      window.dispatchEvent(new Event("yubal:ledger-changed"));
    }
  };

  const handleQuickAddToDirect = async (track: SyncTrackItem) => {
    if (!externalDirName || !track.relative_path) return;
    const id = track.video_id || track.relative_path;
    if (track.in_direct || addedMap[id]) return;
    setMatchingRelPath(track.relative_path);
    const result = await deleteExternalTrack(
      externalDirName,
      track.relative_path,
      "add_to_direct",
    );
    setMatchingRelPath(null);
    if ("error" in result) {
      showErrorToast(t("sync.addToDirect"), result.error);
      return;
    }
    if (result.ok) {
      markAddedToDirect(id);
      setAddedMap(readAddedToDirect());
      reload();
      onMatched?.();
      window.dispatchEvent(new Event("yubal:ledger-changed"));
      showSuccessToast(
        t("sync.addToDirect"),
        t("sync.addToDirectDone"),
      );
    }
  };

  const pendingLabel = pendingDelete
    ? formatArtistTitle(pendingDelete.artist, pendingDelete.title)
    : "";
  const pendingUnblockLabel = pendingUnblock
    ? formatArtistTitle(pendingUnblock.artist, pendingUnblock.title)
    : "";

  if (!open) return null;

  const renderRow = (track: SyncTrackItem) => {
    const quality = isExternal
      ? externalQualityTier(track, allowMutate)
      : null;
    const rowCanDelete = isExternal
      ? quality === "junk_rw" || quality === "junk_ro"
        ? false
        : quality === "raw"
          ? allowMutate
          : Boolean(canDelete)
      : Boolean(canDelete);
    return (
      <TrackRow
        key={`${track.index}-${trackIdentity(track)}`}
        track={track}
        saveFolder={saveFolder}
        canDelete={rowCanDelete}
        allowDeleteWhenMissing={Boolean(subscriptionId) || !isExternal}
        offline={Boolean(
          (track.video_id && offlineIds.has(track.video_id)) ||
            track.membership_status === "offline",
        )}
        offlineKind={
          subscriptionId ? "not_in_playlist" : "id_invalid"
        }
        blocked={Boolean(
          (track.video_id && blockedIds.has(track.video_id)) ||
            track.membership_status === "blocked",
        )}
        displayIndex={displayNumbers.get(trackIdentity(track)) ?? ""}
        busy={
          (busyVideoId != null && busyVideoId === track.video_id) ||
          matchingRelPath === track.relative_path
        }
        playable
        mutable={!isExternal || allowMutate}
        external={isExternal}
        qualityTier={quality}
        onDeleteRequest={setPendingDelete}
        onEditRequest={setPendingEdit}
        onEnrichRequest={(item) => {
          void handleEnrichTrack(item);
        }}
        onDispose={
          subscriptionId
            ? (item, action) => {
                void disposeOffline(item, action);
              }
            : undefined
        }
        onMatchRequest={(item) => {
          void handleMatchTrack(item);
        }}
        onUnblockRequest={setPendingUnblock}
        onDownloadRequest={
          subscriptionId
            ? (item) => {
                void handleDownloadMissing(item);
              }
            : undefined
        }
        onAddToDirect={
          isExternal
            ? (item) => {
                void handleQuickAddToDirect(item);
              }
            : undefined
        }
        addedToDirect={Boolean(
          track.in_direct ||
            addedMap[track.video_id || track.relative_path],
        )}
      />
    );
  };

  return (
    <>
      <div
        className="border-default-200 bg-content2/40 relative z-10 border-t"
        role="region"
        aria-label={t("sync.trackList")}
      >
        {loading || displayTracks === null ? (
          <div className="flex items-center justify-center gap-2 px-3 py-3">
            <Spinner size="sm" />
            <span className="text-foreground-400 text-xs">
              {t("common.loading")}
            </span>
          </div>
        ) : displayTracks.length === 0 && !pinnedTrack ? (
          <p className="text-foreground-400 px-3 py-2 text-xs">
            {t("sync.trackListEmpty")}
          </p>
        ) : (
          <>
            <div className="relative">
              <div>
                {pinnedTrack ? (
                  <ul className="border-default-200 bg-content1 relative z-40 border-b">
                    {renderRow(pinnedTrack)}
                  </ul>
                ) : null}
                <div
                  ref={viewportRef}
                  className={`overflow-y-auto overscroll-contain ${
                    pinnedTrack ? "max-h-[calc(50rem-2rem)]" : "max-h-[50rem]"
                  }`}
                >
                  {indexedMode && sectionsWithIds ? (
                    <ul className="divide-default-100 divide-y">
                      {sectionsWithIds.map((section) => (
                        <li key={section.domId} className="list-none">
                          <div
                            id={section.domId}
                            className="bg-content2/95 text-foreground-500 sticky top-0 z-20 border-default-100 border-b px-3 py-1 text-[10px] font-medium tracking-wide backdrop-blur-sm"
                          >
                            {section.letter}
                            <span className="text-foreground-400 ml-2 font-normal tabular-nums">
                              {section.tracks.length}
                            </span>
                          </div>
                          <ul className="divide-default-100 divide-y">
                            {section.tracks.map((track) => renderRow(track))}
                          </ul>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <ul className="divide-default-100 divide-y">
                      {displayTracks.map((track) => renderRow(track))}
                    </ul>
                  )}
                </div>
              </div>
              {indexedMode && sectionsWithIds ? (
                <SectionIndexRail
                  letters={sectionLetters}
                  inViewLetters={inViewLetters}
                  onJump={jumpToLetter}
                />
              ) : null}
            </div>
          </>
        )}
      </div>

      <TrackEditModal
        track={pendingEdit}
        saveFolder={saveFolder}
        isOpen={pendingEdit !== null}
        readOnlyTags={isExternal && !allowMutate}
        streamPath={
          pendingEdit
            ? audioPath(saveFolder, pendingEdit.relative_path, isExternal)
            : undefined
        }
        onClose={() => setPendingEdit(null)}
        onSaved={handleTagsSaved}
      />

      <TrackDeleteModal
        trackLabel={pendingLabel}
        isOpen={pendingDelete !== null}
        busy={deleting}
        externalEnabled={externalEnabled}
        variant={
          subscriptionId
            ? "subscription"
            : isExternal
              ? pendingDelete?.tier === "raw"
                ? "external_raw"
                : "external"
              : "direct"
        }
        onClose={() => {
          if (!deleting) setPendingDelete(null);
        }}
        onConfirm={(mode) => {
          void confirmDelete(mode);
        }}
      />

      <Modal
        isOpen={matchPick !== null}
        onClose={() => {
          if (!acceptingMatch) {
            setMatchPick(null);
            setSelectedCandidateId(null);
          }
        }}
        placement="center"
        size="lg"
      >
        <ModalContent>
          <ModalHeader>{t("sync.matchPickTitle")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            <p className="text-foreground-500">{t("sync.matchPickBody")}</p>
            {matchPick && matchPick.candidates.length > 0 ? (
              <ul className="border-default-200 max-h-72 overflow-y-auto rounded-md border">
                {matchPick.candidates.map((c) => {
                  const active = selectedCandidateId === c.video_id;
                  const score =
                    c.score != null ? Math.round(c.score) : null;
                  return (
                    <li key={c.video_id}>
                      <button
                        type="button"
                        className={`hover:bg-default-100 flex w-full items-center gap-3 px-3 py-2 text-left ${
                          active ? "bg-primary/10" : ""
                        }`}
                        onClick={() => setSelectedCandidateId(c.video_id)}
                      >
                        {c.thumbnail_url ? (
                          <img
                            src={c.thumbnail_url}
                            alt=""
                            className="h-10 w-10 shrink-0 rounded object-cover"
                          />
                        ) : (
                          <span className="bg-default-100 h-10 w-10 shrink-0 rounded" />
                        )}
                        <span className="min-w-0 flex-1">
                          <span className="block truncate font-medium">
                            {c.title}
                          </span>
                          <span className="text-foreground-400 block truncate text-xs">
                            {c.artists}
                            {c.album ? ` · ${c.album}` : ""}
                          </span>
                        </span>
                        {score != null ? (
                          <span className="text-foreground-400 shrink-0 text-xs tabular-nums">
                            {t("sync.matchPickScore", { score })}
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-foreground-400">{t("sync.matchPickEmpty")}</p>
            )}
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={acceptingMatch}
              onPress={() => {
                setMatchPick(null);
                setSelectedCandidateId(null);
                reload();
                onMatched?.();
              }}
            >
              {t("sync.matchPickCancel")}
            </Button>
            <Button
              color="primary"
              isDisabled={!selectedCandidateId || acceptingMatch}
              isLoading={acceptingMatch}
              onPress={() => {
                void handleAcceptCandidate();
              }}
            >
              {t("sync.matchPickConfirm")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <Modal
        isOpen={pendingUnblock !== null}
        onClose={() => {
          if (!deleting) setPendingUnblock(null);
        }}
        placement="center"
      >
        <ModalContent>
          <ModalHeader>{t("sync.blockedTrackActions")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            <p>{t("sync.blockedTrackBody", { name: pendingUnblockLabel })}</p>
            <div className="flex flex-col gap-2">
              {!subscriptionId ? (
                <Button
                  variant="flat"
                  color="danger"
                  className="justify-start h-auto py-3 whitespace-normal"
                  isDisabled={deleting}
                  onPress={() => {
                    void confirmRemoveBlocked();
                  }}
                >
                  <span className="text-left">
                    <span className="block font-medium">
                      {t("sync.blockedRemoveFromList")}
                    </span>
                    <span className="text-foreground-400 text-xs">
                      {t("sync.blockedRemoveFromListHintDirect")}
                    </span>
                  </span>
                </Button>
              ) : null}
              <Button
                variant="flat"
                color="primary"
                className="justify-start h-auto py-3 whitespace-normal"
                isLoading={deleting}
                onPress={() => {
                  void confirmUnblock();
                }}
              >
                <span className="text-left">
                  <span className="block font-medium">
                    {t("sync.blockedRestoreSync")}
                  </span>
                  <span className="text-foreground-400 text-xs">
                    {subscriptionId
                      ? t("sync.blockedRestoreSyncHint")
                      : t("sync.blockedRestoreSyncHintDirect")}
                  </span>
                </span>
              </Button>
            </div>
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={deleting}
              onPress={() => setPendingUnblock(null)}
            >
              {t("sync.cancel")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </>
  );
}
