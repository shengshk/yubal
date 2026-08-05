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
  listSubscriptions,
  listSubscriptionTracks,
  downloadSubscriptionTrack,
  rateLikedSong,
  unblockSubscriptionTrack,
} from "@/api/subscriptions";
import {
  acceptExternalMatch,
  acceptExternalMeta,
  deleteExternalTrack,
  listExternalPlaylistTracksPage,
  matchExternalTrack,
  type ExternalMatchCandidate,
  type ExternalMetaCandidate,
  type ExternalTrack,
} from "@/api/external";
import {
  addWantedTrack,
  deleteWantedTrack,
  listWantedTracks,
  type WantedTrack,
} from "@/api/wanted";
import { useLibraryAudio } from "@/features/sync/library-audio";
import { FavoriteAction } from "@/features/sync/favorite-action";
import { SectionIndexRail } from "@/features/sync/section-index-rail";
import {
  SYNC_ACTION_BTN,
  TRACK_ACTIONS,
  TRACK_ACTION_SLOT,
  TRACK_INDEX,
  TRACK_INDEX_ICON,
  TRACK_ROW_GRID,
  TrackActionSlot,
  TrackTextCells,
} from "@/features/sync/track-columns";
import {
  TrackDeleteModal,
  type TrackDeleteMode,
} from "@/features/sync/track-delete-modal";
import { TrackEditModal } from "@/features/sync/track-edit-modal";
import {
  DEFAULT_INDEX_THRESHOLD,
  type IndexLetter,
} from "@/features/sync/track-index";
import {
  assignDisplayNumbers,
  BUCKET_PREFIX_HINT_KEY,
  buildOrderedTrackSections,
  displayIndexPrefix,
  lettersInSections,
  orderedSectionDomId,
  resolveJunkKind,
  sortTracksUnified,
  trackIdentity,
  WANTED_PREFIX_HINT_KEY,
  type JunkKind,
} from "@/features/sync/track-list-order";
import { formatArtistTitle } from "@/features/sync/track-label";
import { isLikedMusicUrl } from "@/lib/subscription-labels";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Spinner,
} from "@heroui/react";
import {
  ArchiveIcon,
  AudioLinesIcon,
  BanIcon,
  CloudOffIcon,
  DownloadIcon,
  ExternalLinkIcon,
  HeartIcon,
  ImageIcon,
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
const TRACK_PAGE_SIZE = 100;

function candidateSourceLabel(source: string): string {
  if (source.toLowerCase() === "musicbrainz") return "MusicBrainz";
  if (source.toLowerCase() === "qq") return "QQ";
  if (source.toLowerCase() === "ytm") return "YTM";
  return source;
}

function CandidateThumbnail({ url }: { url?: string | null }) {
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setFailed(false);
  }, [url]);

  if (url && !failed) {
    return (
      <img
        src={url}
        alt=""
        className="h-10 w-10 shrink-0 rounded object-cover"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span className="bg-default-100 text-foreground-300 flex h-10 w-10 shrink-0 items-center justify-center rounded">
      <ImageIcon className="h-4 w-4" />
    </span>
  );
}

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
   * "wanted" reads the wishlist; rows with a hardlinked file are playable.
   */
  mode?: "sync" | "external" | "wanted";
  externalDirName?: string;
  /** Wanted mode: parent owns the delete / per-track YTM match dialogs. */
  onWantedDelete?: (track: SyncTrackItem) => void;
  onWantedMatch?: (track: SyncTrackItem) => void;
  /** Wanted mode: id of the row currently running a match. */
  wantedBusyId?: string | null;
  /** Playlist-level allow_mutate — gates tag edit / raw delete / tag scrape. */
  allowMutate?: boolean;
  /** Playlist-level show_raw — hides unmatched rows when false. */
  showRaw?: boolean;
  /** Playlist-level show_junk — junk is a subset of unmatched. */
  showJunk?: boolean;
  /** Called after a match action completes, so the parent can refresh counts. */
  onMatched?: () => void;
  /** Makes rows in this list remote Likes; clicking cancels the YTM Like. */
  likedSubscriptionId?: string | null;
  /** Only used when two logical sources share the same card. */
  withTopBorder?: boolean;
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

/** Maps a wishlist row onto the shared SyncTrackItem shape. */
function wantedTrackToItem(track: WantedTrack, index: number): SyncTrackItem {
  const hasFile = track.has_file && Boolean(track.relative_path);
  return {
    index,
    title: track.title,
    artist: track.artists,
    album_artist: null,
    album: track.album ?? null,
    exists: hasFile,
    storage: hasFile ? "hardlink" : "missing",
    // Wanted files live on their own root; the stream API keys them by prefix.
    relative_path: hasFile ? `wanted/${track.relative_path}` : "",
    video_id: track.video_id ?? null,
    tier: hasFile ? "complete" : "raw",
    cover_url: track.thumbnail_url,
    tags_complete: true,
    wanted_id: track.id,
    source_url: track.source_url,
    meta_source: track.source,
  };
}

function wantedSoftKey(
  title: string,
  artists: string,
  album?: string | null,
): string {
  return `${title.trim().toLowerCase()}|${artists.trim().toLowerCase()}|${(album ?? "").trim().toLowerCase()}`;
}

function trackWantedKeys(track: SyncTrackItem): string[] {
  const keys = [
    wantedSoftKey(track.title || "", track.artist || "", track.album),
  ];
  const sid = (track.meta_source_id || "").trim();
  if (sid) keys.push(`sid:${sid}`);
  return keys;
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
function externalTrackToItem(
  track: ExternalTrack,
  index: number,
): SyncTrackItem {
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
    video_id: matched ? (track.video_id ?? null) : null,
    tier: matched ? (track.tier ?? "complete") : "raw",
    cover_source: track.cover_source ?? null,
    cover_url: track.cover_url ?? null,
    has_embedded_cover: Boolean(track.has_embedded_cover),
    year: track.year ?? null,
    track_number: track.track_number ?? null,
    tags_complete:
      track.tags_complete ??
      Boolean(
        track.title?.trim() && track.artist?.trim() && track.album?.trim(),
      ),
    meta_status: track.meta_status ?? null,
    meta_source: track.meta_source ?? null,
    meta_source_id: track.meta_source_id ?? null,
    meta_source_url: track.meta_source_url ?? null,
    is_junk: Boolean(track.is_junk) || junkKind != null,
    junk_kind: junkKind,
    in_direct: Boolean(track.in_direct),
    can_mutate: track.can_mutate,
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
  wanted = false,
  wantedEnabled = false,
  qualityTier = null,
  onWantedMatch,
  onDeleteRequest,
  onEditRequest,
  onEnrichRequest,
  onDispose,
  onMatchRequest,
  onUnblockRequest,
  onDownloadRequest,
  onAddToDirect,
  addedToDirect = false,
  inWanted = false,
  wantedTrackId = null,
  remoteLiked = false,
  onLikedToggle,
  onLocalHeartToggle,
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
  wanted?: boolean;
  wantedEnabled?: boolean;
  /** External playlist quality tier; null = non-external / use legacy tier. */
  qualityTier?: ExternalQualityTier | null;
  onWantedMatch?: (track: SyncTrackItem) => void;
  onDeleteRequest: (track: SyncTrackItem) => void;
  onEditRequest: (track: SyncTrackItem) => void;
  onEnrichRequest: (track: SyncTrackItem) => void;
  onDispose?: (
    track: SyncTrackItem,
    action: "archive" | "delete" | "to_wanted",
  ) => void;
  onMatchRequest?: (track: SyncTrackItem) => void;
  onUnblockRequest?: (track: SyncTrackItem) => void;
  onDownloadRequest?: (track: SyncTrackItem) => void;
  onAddToDirect?: (track: SyncTrackItem) => void;
  addedToDirect?: boolean;
  inWanted?: boolean;
  wantedTrackId?: string | null;
  remoteLiked?: boolean;
  onLikedToggle?: (track: SyncTrackItem) => void;
  onLocalHeartToggle?: (track: SyncTrackItem, wantedId?: string | null) => void;
}) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const isRaw = track.tier === "raw";
  // External Raw = unmatched but file present (still playable). Wanted "W" rows
  // reuse tier raw for numbering, yet have no file — treat as missing/dimmed.
  const missing = wanted
    ? !track.exists || track.storage === "missing" || !track.relative_path
    : !isRaw && (!track.exists || track.storage === "missing");
  const filePath = audioPath(
    saveFolder,
    track.relative_path,
    external || wanted,
  );
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
    tier === "junk_rw" ? "rw" : tier === "junk_ro" ? "ro" : null;
  const isJunk = junkKind != null;
  const isReadonlyJunk = junkKind === "ro";
  const isWritableJunk = junkKind === "rw";
  const isPremium = tier === "premium" || track.tier === "premium";
  const isUnmatched = Boolean(isJunk || tier === "raw" || (!tier && isRaw));

  const showMatchOrEnrich = !wanted && !blocked && !missing;
  const showUnmatchedMatch = showMatchOrEnrich && isUnmatched;
  const showMatchedEnrich =
    showMatchOrEnrich && !isUnmatched && Boolean(track.video_id);
  const hasValidYtmId = Boolean(track.video_id) && !offline && !blocked;
  const canUseLocalHeart =
    !wanted &&
    !hasValidYtmId &&
    track.meta_status === "verified" &&
    track.tags_complete === true &&
    Boolean(onLocalHeartToggle);

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
    !wanted &&
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
    <li
      className="relative h-8 overflow-hidden"
      style={{ contentVisibility: "auto", containIntrinsicSize: "32px" }}
    >
      <div
        className={`${TRACK_ROW_GRID} ${
          dimmed ? "text-foreground-400" : "text-foreground"
        }`}
      >
        {/* Play/seek hit target: covers index + text, not the action column. */}
        {!rowInert ? (
          <button
            type="button"
            className="absolute inset-y-0 right-[10.5rem] left-0 z-0 cursor-pointer border-0 bg-transparent p-0"
            aria-label={
              isCurrent
                ? t("sync.seekTrack")
                : `${t("sync.playTrack")}: ${label}`
            }
            title={label}
            onClick={onPlayAreaClick}
          />
        ) : null}

        <span
          className={`${TRACK_INDEX} relative z-10`}
          title={(() => {
            const prefix = displayIndexPrefix(displayIndex);
            const keyMap = wanted
              ? WANTED_PREFIX_HINT_KEY
              : BUCKET_PREFIX_HINT_KEY;
            const key = keyMap[prefix];
            return key ? t(`sync.${key}`) : undefined;
          })()}
        >
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
          <span className={TRACK_ACTION_SLOT}>
            {offline &&
            offlineKind === "id_invalid" &&
            wantedEnabled &&
            onDispose ? (
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled={busy}
                className={`${ACTION_BTN} hover:text-danger`}
                aria-label={t("sync.migrateToWanted")}
                title={t("sync.migrateToWantedHint")}
                onPress={() => onDispose(track, "to_wanted")}
              >
                <HeartIcon className="h-3.5 w-3.5" />
              </Button>
            ) : wanted && track.wanted_id && onLocalHeartToggle ? (
              <FavoriteAction
                kind="local"
                active
                busy={busy}
                className={ACTION_BTN}
                onPress={() => onLocalHeartToggle(track, track.wanted_id)}
              />
            ) : hasValidYtmId && onLikedToggle ? (
              <FavoriteAction
                kind="remote"
                active={remoteLiked}
                busy={busy}
                className={ACTION_BTN}
                onPress={() => onLikedToggle(track)}
              />
            ) : canUseLocalHeart ? (
              <FavoriteAction
                kind="local"
                active={inWanted}
                busy={busy}
                className={ACTION_BTN}
                onPress={() => onLocalHeartToggle!(track, wantedTrackId)}
              />
            ) : (
              <FavoriteAction
                kind={track.video_id ? "remote" : "local"}
                active={false}
                disabled
                className={ACTION_BTN}
              />
            )}
          </span>
          <TrackActionSlot
            fallbackIcon={<ExternalLinkIcon className="h-3.5 w-3.5" />}
            fallbackLabel={t("sync.openInYtm")}
          >
            {wanted && track.source_url ? (
              <Button
                as="a"
                href={track.source_url}
                target="_blank"
                rel="noopener noreferrer"
                variant="light"
                size="sm"
                isIconOnly
                className={`${ACTION_BTN} hover:text-primary`}
                aria-label={t("sync.wantedOpenSource")}
                title={t("sync.wantedOpenSource")}
              >
                <ExternalLinkIcon className="h-3.5 w-3.5" />
              </Button>
            ) : track.video_id ? (
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
          </TrackActionSlot>
          <TrackActionSlot
            fallbackIcon={<DownloadIcon className="h-3.5 w-3.5" />}
            fallbackLabel={t("sync.trackDownload")}
          >
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
            ) : showMatchedEnrich && onAddToDirect ? (
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled={busy || addedToDirect}
                className={`${ACTION_BTN} ${
                  addedToDirect
                    ? "!text-success-600 opacity-100"
                    : "text-success hover:text-success"
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
                  if (!addedToDirect) onAddToDirect(track);
                }}
              >
                <PlusIcon className="h-3.5 w-3.5" />
              </Button>
            ) : null}
          </TrackActionSlot>
          <TrackActionSlot
            fallbackIcon={<PencilIcon className="h-3.5 w-3.5" />}
            fallbackLabel={t("sync.editTrackTags")}
          >
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
          </TrackActionSlot>
          <TrackActionSlot
            fallbackIcon={<SparklesIcon className="h-3.5 w-3.5" />}
            fallbackLabel={t("sync.matchTrack")}
          >
            {wanted && onWantedMatch ? (
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isLoading={busy}
                className={
                  track.video_id
                    ? `${ACTION_BTN} hover:text-primary`
                    : `${ACTION_BTN} !text-danger hover:!text-danger-600`
                }
                aria-label={t("sync.wantedMatchYtm")}
                title={t("sync.wantedMatchYtm")}
                onPress={() => {
                  if (!busy) onWantedMatch(track);
                }}
              >
                {!busy ? <SparklesIcon className="h-3.5 w-3.5" /> : null}
              </Button>
            ) : showUnmatchedMatch ? (
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
            ) : showMatchedEnrich ? (
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
            ) : blocked ? (
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
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled={busy}
                className={`${ACTION_BTN} hover:text-primary`}
                aria-label={t("sync.idInvalidActionToRawDelete")}
                title={t("sync.cleanupActionArchiveHint")}
                onPress={() => onDispose(track, "archive")}
              >
                <ArchiveIcon className="h-3.5 w-3.5" />
              </Button>
            ) : offline ? (
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled
                className={`${ACTION_BTN} text-warning opacity-40`}
                aria-label={offlineBadgeLabel}
                title={offlineBadgeLabel}
              >
                <CloudOffIcon className="h-3.5 w-3.5" />
              </Button>
            ) : null}
          </TrackActionSlot>
          <TrackActionSlot
            fallbackIcon={<Trash2Icon className="h-3.5 w-3.5" />}
            fallbackLabel={t("sync.deleteTrack")}
          >
            {offline && onDispose ? (
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isDisabled={busy}
                className={`${ACTION_BTN} hover:text-danger`}
                aria-label={t("sync.idInvalidActionDelete")}
                title={t("sync.cleanupActionDeleteHint")}
                onPress={() => onDispose(track, "delete")}
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </Button>
            ) : canDelete ? (
              <Button
                variant="light"
                size="sm"
                isIconOnly
                className={`${ACTION_BTN} hover:text-danger`}
                isDisabled={missing && !allowDeleteWhenMissing}
                aria-label={t("sync.deleteTrack")}
                title={t("sync.deleteTrack")}
                onPress={() => onDeleteRequest(track)}
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </Button>
            ) : null}
          </TrackActionSlot>
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
  onWantedDelete,
  onWantedMatch,
  wantedBusyId,
  likedSubscriptionId = null,
  withTopBorder = true,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const isExternal = mode === "external";
  const isWanted = mode === "wanted";
  // External and Wanted files are streamed by their own root-prefixed path.
  const rawPath = isExternal || isWanted;
  const [tracks, setTracks] = useState<SyncTrackItem[] | null>(null);
  const [offlineIds, setOfflineIds] = useState<Set<string>>(new Set());
  const [idInvalidIds, setIdInvalidIds] = useState<Set<string>>(new Set());
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
    metaCandidates: ExternalMetaCandidate[];
  } | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(
    null,
  );
  const [selectedMetaKey, setSelectedMetaKey] = useState<string | null>(null);
  const [acceptingMatch, setAcceptingMatch] = useState(false);
  const [wantedKeys, setWantedKeys] = useState<Set<string>>(new Set());
  const [wantedTrackIds, setWantedTrackIds] = useState<Map<string, string>>(
    new Map(),
  );
  const [likedVideoIds, setLikedVideoIds] = useState<Set<string>>(new Set());
  const [favoriteSubscriptionId, setFavoriteSubscriptionId] = useState<
    string | null
  >(null);
  const [addingWantedPath, setAddingWantedPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [externalNextOffset, setExternalNextOffset] = useState<number | null>(
    null,
  );
  const [externalTotal, setExternalTotal] = useState(0);
  const [externalEnabled, setExternalEnabled] = useState(false);
  const [wantedEnabled, setWantedEnabled] = useState(false);
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
  const [renderLimit, setRenderLimit] = useState(TRACK_PAGE_SIZE);

  const reload = useCallback(() => {
    if (!open) return;
    setRenderLimit(TRACK_PAGE_SIZE);

    if (isWanted) {
      const token = ++reloadToken.current;
      setLoading(true);
      void listWantedTracks().then((items) => {
        if (token !== reloadToken.current) return;
        setTracks(items.map((item, i) => wantedTrackToItem(item, i)));
        setOfflineIds(new Set());
        setBlockedIds(new Set());
        setOfflineMembershipIds(new Map());
        setOfflineExtra([]);
        setLoading(false);
      });
      return;
    }

    if (isExternal) {
      if (!externalDirName) return;
      const token = ++reloadToken.current;
      setLoading(true);
      setLoadingMore(false);
      void listExternalPlaylistTracksPage(externalDirName, {
        offset: 0,
        limit: TRACK_PAGE_SIZE,
        refresh: true,
      }).then((page) => {
        if (token !== reloadToken.current) return;
        const mapped = page.items
          .map((item, i) => externalTrackToItem(item, page.offset + i + 1))
          .filter((item) => showRaw || item.tier !== "raw")
          .filter((item) => showJunk || !item.is_junk);
        setTracks(mapped);
        setExternalTotal(page.total);
        setExternalNextOffset(page.next_offset);
        setOfflineIds(new Set());
        setIdInvalidIds(new Set());
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
          const idInvalid = members.filter(
            (member) => member.membership_status === "id_invalid",
          );
          const blocked = members.filter(
            (member) => member.membership_status === "blocked",
          );
          const active = members.filter(
            (member) => member.membership_status === "active",
          );
          // ID-invalid rows are treated like offline (dimmed + dispose); a
          // separate set drives the badge label.
          const offlineById = new Set(
            [...offline, ...idInvalid].map(
              (m) => m.catalog_video_id || m.video_id,
            ),
          );
          const idInvalidById = new Set(
            idInvalid.map((m) => m.catalog_video_id || m.video_id),
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
          const logicalExtras = [
            ...active,
            ...offline,
            ...idInvalid,
            ...blocked,
          ]
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
          setIdInvalidIds(idInvalidById);
          setBlockedIds(blockedById);
          setOfflineMembershipIds(membershipIds);
          setOfflineExtra(logicalExtras);
        } else {
          const offlineById = new Set(
            items
              .filter((it) => it.membership_status === "offline" && it.video_id)
              .map((it) => it.video_id as string),
          );
          setIdInvalidIds(new Set());
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
  }, [
    open,
    saveFolder,
    subscriptionId,
    isExternal,
    isWanted,
    externalDirName,
    showRaw,
    showJunk,
  ]);

  const loadMoreExternal = useCallback(() => {
    if (
      !open ||
      !isExternal ||
      !externalDirName ||
      externalNextOffset === null ||
      loading ||
      loadingMore
    ) {
      return;
    }
    const token = reloadToken.current;
    const offset = externalNextOffset;
    setLoadingMore(true);
    void listExternalPlaylistTracksPage(externalDirName, {
      offset,
      limit: TRACK_PAGE_SIZE,
    }).then((page) => {
      if (token !== reloadToken.current) return;
      const mapped = page.items
        .map((item, i) => externalTrackToItem(item, page.offset + i + 1))
        .filter((item) => showRaw || item.tier !== "raw")
        .filter((item) => showJunk || !item.is_junk);
      setTracks((current) => [...(current ?? []), ...mapped]);
      setExternalTotal(page.total);
      setExternalNextOffset(page.next_offset);
      setLoadingMore(false);
    });
  }, [
    externalDirName,
    externalNextOffset,
    isExternal,
    loading,
    loadingMore,
    open,
    showJunk,
    showRaw,
  ]);

  useEffect(() => {
    if (!open) {
      setTracks(null);
      setRenderLimit(TRACK_PAGE_SIZE);
      setExternalNextOffset(null);
      setExternalTotal(0);
      setLoadingMore(false);
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
        setWantedEnabled(Boolean(settings.wanted_enabled));
      });
    };
    loadSettings();
    window.addEventListener("yubal:settings-changed", loadSettings);
    return () => {
      window.removeEventListener("yubal:settings-changed", loadSettings);
    };
  }, []);

  useEffect(() => {
    if (!open || !wantedEnabled) {
      setWantedKeys(new Set());
      setWantedTrackIds(new Map());
      return;
    }
    let cancelled = false;
    const loadWanted = async () => {
      const rows = await listWantedTracks();
      if (cancelled) return;
      const next = new Set<string>();
      const ids = new Map<string, string>();
      for (const row of rows) {
        const soft = wantedSoftKey(row.title, row.artists, row.album);
        next.add(soft);
        ids.set(soft, row.id);
        const sid = (row.source_id || row.video_id || "").trim();
        if (sid) {
          const key = `sid:${sid}`;
          next.add(key);
          ids.set(key, row.id);
        }
      }
      setWantedKeys(next);
      setWantedTrackIds(ids);
    };
    void loadWanted();
    const onChanged = () => {
      void loadWanted();
    };
    window.addEventListener("yubal:ledger-changed", onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("yubal:ledger-changed", onChanged);
    };
  }, [open, wantedEnabled]);

  useEffect(() => {
    if (!open || isWanted) return;
    let cancelled = false;
    const loadLiked = async () => {
      const liked = (await listSubscriptions()).find((sub) =>
        isLikedMusicUrl(sub.url),
      );
      if (cancelled) return;
      setFavoriteSubscriptionId(liked?.id ?? null);
      if (!liked) {
        setLikedVideoIds(new Set());
        return;
      }
      const rows = await listSubscriptionTracks(liked.id);
      if (cancelled) return;
      setLikedVideoIds(
        new Set(
          rows
            .filter((row) => row.membership_status === "active")
            .map((row) => row.video_id),
        ),
      );
    };
    void loadLiked();
    const onChanged = () => void loadLiked();
    window.addEventListener("yubal:ledger-changed", onChanged);
    return () => {
      cancelled = true;
      window.removeEventListener("yubal:ledger-changed", onChanged);
    };
  }, [open, isWanted]);

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
    const sorted = sortTracksUnified(combinedTracks, trackSortKey, orderCtx);
    if (!isWanted) return sorted;
    // Wishlist: hardlinked rows first, tags-only wishes after.
    return [
      ...sorted.filter((track) => track.relative_path),
      ...sorted.filter((track) => !track.relative_path),
    ];
  }, [combinedTracks, trackSortKey, orderCtx, isWanted]);

  const renderedOrderedTracks = useMemo(
    () => orderedTracks?.slice(0, renderLimit) ?? null,
    [orderedTracks, renderLimit],
  );

  const indexedMode = Boolean(
    renderedOrderedTracks && renderedOrderedTracks.length >= indexThreshold,
  );

  const sections = useMemo(() => {
    if (!renderedOrderedTracks || !indexedMode) return null;
    return buildOrderedTrackSections(renderedOrderedTracks, trackSortKey);
  }, [renderedOrderedTracks, indexedMode, trackSortKey]);

  /** Current track in this folder (playing or paused) — pinned above the list. */
  const pinnedTrack = useMemo(() => {
    if (!combinedTracks || audio.activeFolder !== saveFolder || !audio.key) {
      return null;
    }
    return (
      combinedTracks.find(
        (track) =>
          audioPath(saveFolder, track.relative_path, rawPath) === audio.key,
      ) ?? null
    );
  }, [combinedTracks, audio.activeFolder, audio.key, saveFolder, rawPath]);

  const pinnedKey = pinnedTrack
    ? audioPath(saveFolder, pinnedTrack.relative_path, rawPath)
    : null;

  /** Flat display order: playing pinned, then unified bucket sort. */
  const displayTracks = useMemo(() => {
    if (!renderedOrderedTracks) return null;
    if (!pinnedKey) return renderedOrderedTracks;
    return renderedOrderedTracks.filter(
      (track) =>
        audioPath(saveFolder, track.relative_path, rawPath) !== pinnedKey,
    );
  }, [renderedOrderedTracks, pinnedKey, saveFolder, rawPath]);

  const listSections = useMemo(() => {
    if (!sections) return null;
    if (!pinnedKey) return sections;
    return sections
      .map((section) => ({
        ...section,
        tracks: section.tracks.filter(
          (track) =>
            audioPath(saveFolder, track.relative_path, rawPath) !== pinnedKey,
        ),
      }))
      .filter((section) => section.tracks.length > 0);
  }, [sections, pinnedKey, saveFolder, rawPath]);

  const sectionLetters = useMemo(
    () => (listSections ? lettersInSections(listSections) : []),
    [listSections],
  );

  useEffect(() => {
    if (!orderedTracks) return;
    const rest = pinnedKey
      ? orderedTracks.filter(
          (track) =>
            audioPath(saveFolder, track.relative_path, rawPath) !== pinnedKey,
        )
      : orderedTracks;
    const ordered = pinnedTrack ? [pinnedTrack, ...rest] : rest;
    audio.registerPlaylist(
      saveFolder,
      ordered
        .filter((t) => t.exists && t.storage !== "missing")
        .map((t) => {
          const path = audioPath(saveFolder, t.relative_path, rawPath);
          return {
            key: path,
            path,
            label: formatArtistTitle(t.artist, t.title),
          };
        }),
    );
  }, [audio, saveFolder, orderedTracks, pinnedTrack, pinnedKey, rawPath]);

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

  useEffect(() => {
    const root = viewportRef.current;
    if (!root || !isExternal || externalNextOffset === null) return;
    const maybeLoad = () => {
      if (root.scrollHeight - root.scrollTop - root.clientHeight < 192) {
        loadMoreExternal();
      }
    };
    maybeLoad();
    root.addEventListener("scroll", maybeLoad, { passive: true });
    return () => root.removeEventListener("scroll", maybeLoad);
  }, [externalNextOffset, isExternal, loadMoreExternal]);

  useEffect(() => {
    const root = viewportRef.current;
    if (
      !root ||
      isExternal ||
      !orderedTracks ||
      renderLimit >= orderedTracks.length
    ) {
      return;
    }
    const maybeReveal = () => {
      if (root.scrollHeight - root.scrollTop - root.clientHeight < 192) {
        setRenderLimit((current) =>
          Math.min(current + TRACK_PAGE_SIZE, orderedTracks.length),
        );
      }
    };
    maybeReveal();
    root.addEventListener("scroll", maybeReveal, { passive: true });
    return () => root.removeEventListener("scroll", maybeReveal);
  }, [isExternal, orderedTracks, renderLimit]);

  const displayNumbers = useMemo(() => {
    if (!combinedTracks) return new Map<string, string>();
    return assignDisplayNumbers(combinedTracks, trackSortKey, orderCtx);
  }, [combinedTracks, trackSortKey, orderCtx]);

  /** Local-heart numbering: R for cloud-like recovery, H for local hearts. */
  const wantedNumbers = useMemo(() => {
    const map = new Map<string, string>();
    if (!isWanted || !orderedTracks) return map;
    let recovery = 0;
    let localHeart = 0;
    for (const track of orderedTracks) {
      if (!track.wanted_id) continue;
      if (track.meta_source === "liked_recovery") {
        recovery += 1;
        map.set(track.wanted_id, `R${recovery}`);
      } else {
        localHeart += 1;
        map.set(track.wanted_id, `H${localHeart}`);
      }
    }
    return map;
  }, [isWanted, orderedTracks]);

  const disposeOffline = async (
    track: SyncTrackItem,
    action: "archive" | "delete" | "to_wanted",
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

  const toggleRemoteLike = async (track: SyncTrackItem) => {
    const subscriptionId = likedSubscriptionId || favoriteSubscriptionId;
    if (!subscriptionId || !track.video_id || busyVideoId) return;
    const liked = likedVideoIds.has(track.video_id);
    setBusyVideoId(track.video_id);
    const result = await rateLikedSong(subscriptionId, track.video_id, !liked);
    setBusyVideoId(null);
    if (!result.success) {
      showErrorToast(
        liked ? t("sync.unlikeYtm") : t("sync.likeYtm"),
        result.error,
      );
      return;
    }
    setLikedVideoIds((current) => {
      const next = new Set(current);
      if (liked) next.delete(track.video_id!);
      else next.add(track.video_id!);
      return next;
    });
    showSuccessToast(
      liked ? t("sync.unlikeYtm") : t("sync.likeYtm"),
      liked ? t("sync.unlikeYtmQueued") : t("sync.likeYtmQueued"),
    );
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
    const metaCandidates = result.meta_candidates ?? [];
    if (candidates.length > 0 || metaCandidates.length > 0) {
      setSelectedCandidateId(candidates[0]?.video_id ?? null);
      setSelectedMetaKey(
        candidates.length > 0
          ? null
          : metaCandidates[0]
            ? `${metaCandidates[0].source}:${metaCandidates[0].source_id}`
            : null,
      );
      setMatchPick({ track, candidates, metaCandidates });
      return;
    }
    showSuccessToast(t("sync.matchDoneTitle"), t("sync.matchTrackNoCandidate"));
    reload();
    onMatched?.();
  };

  const handleAcceptCandidate = async () => {
    if (!matchPick || acceptingMatch) return;
    if (selectedCandidateId) {
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
      setSelectedMetaKey(null);
      showSuccessToast(t("sync.matchDoneTitle"), t("sync.matchTrackMatched"));
      reload();
      onMatched?.();
      return;
    }
    if (selectedMetaKey) {
      const selected = matchPick.metaCandidates.find(
        (c) => `${c.source}:${c.source_id}` === selectedMetaKey,
      );
      if (!selected) return;
      setAcceptingMatch(true);
      const result = await acceptExternalMeta({
        rel_path: matchPick.track.relative_path,
        source: selected.source,
        source_id: selected.source_id,
        title: selected.title,
        artists: selected.artists,
        album: selected.album,
        source_url: selected.source_url,
        thumbnail_url: selected.thumbnail_url,
      });
      setAcceptingMatch(false);
      if ("error" in result) {
        showErrorToast(t("sync.matchFailedTitle"), result.error);
        return;
      }
      setMatchPick(null);
      setSelectedCandidateId(null);
      setSelectedMetaKey(null);
      showSuccessToast(t("sync.metaVerifyDoneTitle"), t("sync.metaVerifyDone"));
      reload();
      onMatched?.();
    }
  };

  const handleAddMetaToWanted = async (track: SyncTrackItem) => {
    if (addingWantedPath) return;
    if (trackWantedKeys(track).some((k) => wantedKeys.has(k))) return;
    setAddingWantedPath(track.relative_path);
    const result = await addWantedTrack({
      title: track.title,
      artists: track.artist || "",
      album: track.album || "",
      source: track.meta_source || "manual",
      source_id: track.meta_source_id || "",
      source_url: track.meta_source_url || undefined,
    });
    setAddingWantedPath(null);
    if ("error" in result) {
      showErrorToast(t("search.addToWanted"), result.error);
      return;
    }
    setWantedKeys((current) => {
      const next = new Set(current);
      for (const key of trackWantedKeys(track)) next.add(key);
      const row = result.data;
      next.add(wantedSoftKey(row.title, row.artists, row.album));
      const sid = (row.source_id || "").trim();
      if (sid) next.add(`sid:${sid}`);
      return next;
    });
    showSuccessToast(t("search.addToWanted"), t("search.addedToWanted"));
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const toggleLocalHeart = async (
    track: SyncTrackItem,
    wantedId?: string | null,
  ) => {
    if (addingWantedPath) return;
    if (wantedId) {
      setAddingWantedPath(track.relative_path || wantedId);
      const ok = await deleteWantedTrack(wantedId, "remove");
      setAddingWantedPath(null);
      if (!ok) {
        showErrorToast(
          t("sync.removeLocalHeart"),
          t("sync.favoriteActionFailed"),
        );
        return;
      }
      window.dispatchEvent(new Event("yubal:ledger-changed"));
      return;
    }
    await handleAddMetaToWanted(track);
  };

  const handleTagsSaved = (result: {
    locations: Array<{
      save_folder: string;
      old_relative_path: string;
      new_relative_path: string;
    }>;
  }) => {
    const moved = result.locations.find(
      (loc) => loc.save_folder === saveFolder,
    );
    if (
      moved &&
      audio.key === audioPath(saveFolder, moved.old_relative_path, rawPath)
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
          const id = pendingDelete.video_id || pendingDelete.relative_path;
          markAddedToDirect(id);
          setAddedMap(readAddedToDirect());
        }
        const key = audioPath(saveFolder, pendingDelete.relative_path, true);
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
      if (mode === "migrate_to_wanted") {
        ok = await disposeSubscriptionTrack(
          subscriptionId,
          membershipVideoId,
          "to_wanted",
        );
      } else {
        ok = await deleteSubscriptionTrackFile(
          subscriptionId,
          membershipVideoId,
          mode === "block",
        );
      }
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
      showErrorToast(
        t("sync.trackDownload"),
        result.error || t("sync.trackDownloadFailed"),
      );
      return;
    }
    showSuccessToast(t("sync.trackDownload"), t("sync.trackDownloadQueued"));
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
      showSuccessToast(t("sync.addToDirect"), t("sync.addToDirectDone"));
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
    const rowMutable = isExternal ? (track.can_mutate ?? allowMutate) : true;
    const quality = isExternal ? externalQualityTier(track, rowMutable) : null;
    const rowCanDelete = isExternal
      ? quality === "junk_rw" || quality === "junk_ro"
        ? false
        : quality === "raw"
          ? rowMutable
          : Boolean(canDelete) && rowMutable
      : Boolean(canDelete);
    return (
      <TrackRow
        key={`${track.index}-${track.wanted_id ?? trackIdentity(track)}`}
        track={track}
        saveFolder={saveFolder}
        canDelete={isWanted ? true : rowCanDelete}
        allowDeleteWhenMissing={
          isWanted || Boolean(subscriptionId) || !isExternal
        }
        offline={Boolean(
          (track.video_id && offlineIds.has(track.video_id)) ||
          track.membership_status === "offline" ||
          track.membership_status === "id_invalid",
        )}
        offlineKind={
          (track.video_id && idInvalidIds.has(track.video_id)) ||
          track.membership_status === "id_invalid"
            ? "id_invalid"
            : subscriptionId
              ? "not_in_playlist"
              : "id_invalid"
        }
        blocked={Boolean(
          (track.video_id && blockedIds.has(track.video_id)) ||
          track.membership_status === "blocked",
        )}
        displayIndex={
          isWanted
            ? (wantedNumbers.get(track.wanted_id ?? "") ?? "")
            : (displayNumbers.get(trackIdentity(track)) ?? "")
        }
        busy={
          (isWanted &&
            wantedBusyId != null &&
            wantedBusyId === track.wanted_id) ||
          (busyVideoId != null && busyVideoId === track.video_id) ||
          (!isWanted && matchingRelPath === track.relative_path) ||
          (!isWanted && addingWantedPath === track.relative_path)
        }
        playable
        mutable={!isExternal || rowMutable}
        external={isExternal}
        wanted={isWanted}
        wantedEnabled={wantedEnabled}
        qualityTier={quality}
        onWantedMatch={
          isWanted && onWantedMatch ? (item) => onWantedMatch(item) : undefined
        }
        onDeleteRequest={
          isWanted && onWantedDelete ? onWantedDelete : setPendingDelete
        }
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
          track.in_direct || addedMap[track.video_id || track.relative_path],
        )}
        inWanted={trackWantedKeys(track).some((k) => wantedKeys.has(k))}
        wantedTrackId={
          trackWantedKeys(track)
            .map((key) => wantedTrackIds.get(key))
            .find(Boolean) ?? null
        }
        remoteLiked={Boolean(
          track.video_id && likedVideoIds.has(track.video_id),
        )}
        onLikedToggle={
          !isWanted && (likedSubscriptionId || favoriteSubscriptionId)
            ? toggleRemoteLike
            : undefined
        }
        onLocalHeartToggle={toggleLocalHeart}
      />
    );
  };

  return (
    <>
      <div
        className={`bg-content2/40 relative z-10 ${
          withTopBorder ? "border-default-200 border-t" : ""
        }`}
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
                            className="bg-content2/95 text-foreground-500 border-default-100 sticky top-0 z-20 border-b px-3 py-1 text-[10px] font-medium tracking-wide backdrop-blur-sm"
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
                  {loadingMore ? (
                    <div className="text-foreground-400 flex h-10 items-center justify-center gap-2 text-xs">
                      <Spinner size="sm" />
                      <span>
                        {tracks?.length ?? 0} / {externalTotal}
                      </span>
                    </div>
                  ) : null}
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
            ? audioPath(saveFolder, pendingEdit.relative_path, rawPath)
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
        wantedEnabled={wantedEnabled}
        offline={Boolean(
          pendingDelete &&
          ((pendingDelete.video_id && offlineIds.has(pendingDelete.video_id)) ||
            pendingDelete.membership_status === "offline" ||
            pendingDelete.membership_status === "id_invalid"),
        )}
        allowMigrateToWanted={Boolean(
          pendingDelete &&
          (subscriptionId
            ? (pendingDelete.video_id &&
                idInvalidIds.has(pendingDelete.video_id)) ||
              pendingDelete.membership_status === "id_invalid"
            : // Direct offline == ID invalid
              (pendingDelete.video_id &&
                offlineIds.has(pendingDelete.video_id)) ||
              pendingDelete.membership_status === "offline" ||
              pendingDelete.membership_status === "id_invalid"),
        )}
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
            setSelectedMetaKey(null);
          }
        }}
        placement="center"
        size="lg"
      >
        <ModalContent>
          <ModalHeader>{t("sync.matchPickTitle")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            <p className="text-foreground-500">{t("sync.matchPickBody")}</p>
            <div className="flex flex-col gap-1">
              <p className="text-foreground-600 text-xs font-medium">
                {t("sync.matchPickYtmSection")}
              </p>
              {matchPick && matchPick.candidates.length > 0 ? (
                <ul className="border-default-200 max-h-48 overflow-y-auto rounded-md border">
                  {matchPick.candidates.map((c) => {
                    const active = selectedCandidateId === c.video_id;
                    const score = c.score != null ? Math.round(c.score) : null;
                    return (
                      <li key={c.video_id}>
                        <button
                          type="button"
                          className={`hover:bg-default-100 flex w-full items-center gap-3 px-3 py-2 text-left ${
                            active ? "bg-primary/10" : ""
                          }`}
                          onClick={() => {
                            setSelectedCandidateId(c.video_id);
                            setSelectedMetaKey(null);
                          }}
                        >
                          <CandidateThumbnail url={c.thumbnail_url} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium">
                              {c.title}
                            </span>
                            <span className="text-foreground-400 block truncate text-xs">
                              {c.artists}
                              {c.album ? ` · ${c.album}` : ""}
                            </span>
                          </span>
                          <span className="text-foreground-400 shrink-0 text-xs tabular-nums">
                            {score != null
                              ? t("sync.matchPickSourceScore", {
                                  source: "YTM",
                                  score,
                                })
                              : "YTM"}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-foreground-400 text-xs">
                  {t("sync.matchPickYtmEmpty")}
                </p>
              )}
            </div>
            <div className="flex flex-col gap-1">
              <p className="text-foreground-600 text-xs font-medium">
                {t("sync.matchPickMetaSection")}
              </p>
              {matchPick && matchPick.metaCandidates.length > 0 ? (
                <ul className="border-default-200 max-h-48 overflow-y-auto rounded-md border">
                  {matchPick.metaCandidates.map((c) => {
                    const key = `${c.source}:${c.source_id}`;
                    const active = selectedMetaKey === key;
                    const score = c.score != null ? Math.round(c.score) : null;
                    return (
                      <li key={key}>
                        <button
                          type="button"
                          className={`hover:bg-default-100 flex w-full items-center gap-3 px-3 py-2 text-left ${
                            active ? "bg-primary/10" : ""
                          }`}
                          onClick={() => {
                            setSelectedMetaKey(key);
                            setSelectedCandidateId(null);
                          }}
                        >
                          <CandidateThumbnail url={c.thumbnail_url} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate font-medium">
                              {c.title}
                            </span>
                            <span className="text-foreground-400 block truncate text-xs">
                              {c.artists}
                              {c.album ? ` · ${c.album}` : ""}
                            </span>
                          </span>
                          <span className="text-foreground-400 shrink-0 text-xs tabular-nums">
                            {score != null
                              ? t("sync.matchPickSourceScore", {
                                  source: candidateSourceLabel(c.source),
                                  score,
                                })
                              : candidateSourceLabel(c.source)}
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-foreground-400 text-xs">
                  {t("sync.matchPickMetaEmpty")}
                </p>
              )}
            </div>
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={acceptingMatch}
              onPress={() => {
                setMatchPick(null);
                setSelectedCandidateId(null);
                setSelectedMetaKey(null);
                reload();
                onMatched?.();
              }}
            >
              {t("sync.matchPickCancel")}
            </Button>
            <Button
              color="primary"
              isDisabled={
                (!selectedCandidateId && !selectedMetaKey) || acceptingMatch
              }
              isLoading={acceptingMatch}
              onPress={() => {
                void handleAcceptCandidate();
              }}
            >
              {selectedMetaKey && !selectedCandidateId
                ? t("sync.matchPickConfirmMeta")
                : t("sync.matchPickConfirm")}
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
                  className="h-auto justify-start py-3 whitespace-normal"
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
                className="h-auto justify-start py-3 whitespace-normal"
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
