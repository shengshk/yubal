import {
  deleteExternalPlaylist,
  listExternalPlaylistTracks,
  syncExternalPlaylist,
  type ExternalPlaylist,
} from "@/api/external";
import { bestCoverUrl, playlistCoverUrl } from "@/api/library";
import { AudioSpectrum } from "@/features/sync/audio-spectrum";
import { LedgerTrackList } from "@/features/sync/ledger-track-list";
import { useLibraryAudio } from "@/features/sync/library-audio";
import type { PlayMode } from "@/features/sync/play-mode";
import { PlaylistTitleTooltip } from "@/features/sync/playlist-title-tooltip";
import { UnifiedPlaylistStats } from "@/features/sync/unified-playlist-stats";
import {
  SYNC_ACTION_BTN,
  SYNC_CARD_ACTIONS,
  SYNC_CARD_CONTENT,
  SYNC_CARD_HEADER,
} from "@/features/sync/track-columns";
import { formatDateTime } from "@/lib/format";
import {
  externalPlaylistDisplayName,
  specialExternalPit,
} from "@/lib/playlist-labels";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import {
  Button,
  Card,
  CardBody,
  Checkbox,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import {
  ArchiveIcon,
  CaptionsIcon,
  FolderIcon,
  Music2Icon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  RefreshCwIcon,
  Repeat1Icon,
  RepeatIcon,
  ShuffleIcon,
  SkipBackIcon,
  SkipForwardIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  playlist: ExternalPlaylist;
  tracksOpen: boolean;
  schedulerEnabled?: boolean;
  onToggleTracks: () => void;
  onCollapseTracks: () => void;
  onEdit: (playlist: ExternalPlaylist) => void;
  onDelete: (playlist: ExternalPlaylist) => void;
  onChanged: () => void;
};

const ACTION_BTN = SYNC_ACTION_BTN;
const LINE =
  "text-foreground-500 mt-1 truncate whitespace-nowrap font-mono text-xs leading-relaxed";

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

export function ExternalPlaylistCard({
  playlist,
  tracksOpen,
  schedulerEnabled = true,
  onToggleTracks,
  onCollapseTracks,
  onEdit,
  onDelete,
  onChanged,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const rootRef = useRef<HTMLDivElement>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncModalOpen, setSyncModalOpen] = useState(false);
  const [syncEnrich, setSyncEnrich] = useState(true);
  const [syncRawMatch, setSyncRawMatch] = useState(true);
  const [syncVerifyMeta, setSyncVerifyMeta] = useState(true);
  const [syncJunkMatch, setSyncJunkMatch] = useState(false);
  const [coverIdx, setCoverIdx] = useState(0);
  const [addAllOpen, setAddAllOpen] = useState(false);
  const [addingAll, setAddingAll] = useState(false);
  const [addMode, setAddMode] = useState<
    "add_matched_to_direct" | "add_meta_verified_to_wanted"
  >("add_matched_to_direct");
  const metaVerifiedCount = playlist.meta_verified_count ?? 0;
  const canAdd = playlist.matched_count > 0 || metaVerifiedCount > 0;

  const folder = `External/${playlist.dir_name}`;
  const syncTone = specialExternalPit(playlist.dir_name)
    ? "text-warning hover:text-warning"
    : playlist.access_mode === "managed"
      ? "text-success hover:text-success"
      : playlist.access_mode === "readonly"
        ? "text-danger hover:text-danger"
        : "text-warning hover:text-warning";
  const isPlayingHere = audio.activeFolder === folder && audio.playing;
  const specialPit = specialExternalPit(playlist.dir_name);
  const preparingPlaylistRef = useRef<Promise<number> | null>(null);

  const preparePlaylist = () => {
    if (preparingPlaylistRef.current) return preparingPlaylistRef.current;
    const request = listExternalPlaylistTracks(playlist.dir_name)
      .then((items) => {
        const playable = items.filter(
          (track) =>
            track.match_status === "matched" &&
            Boolean(track.video_id) &&
            !track.is_raw &&
            track.exists !== false,
        );
        audio.registerPlaylist(
          folder,
          playable.map((track) => ({
            key: track.rel_path,
            path: track.rel_path,
            label:
              [track.artist, track.title].filter(Boolean).join(" - ") ||
              track.title,
          })),
        );
        return playable.length;
      })
      .finally(() => {
        preparingPlaylistRef.current = null;
      });
    preparingPlaylistRef.current = request;
    return request;
  };

  const playingTrackKey =
    audio.activeFolder === folder && audio.key ? audio.key : null;

  const coverCandidates = useMemo(() => {
    const list: string[] = [];
    const push = (url: string | null | undefined) => {
      if (url && !list.includes(url)) list.push(url);
    };
    if (playingTrackKey) {
      push(bestCoverUrl(playingTrackKey));
    } else if (playlist.cover_track_path) {
      push(bestCoverUrl(playlist.cover_track_path));
    }

    const hasKnownContent =
      playlist.matched_count +
        playlist.unmatched_count +
        (playlist.meta_verified_count ?? 0) >
      0;
    if (!specialPit || hasKnownContent) push(playlistCoverUrl(folder));
    return list;
  }, [
    folder,
    playlist.cover_track_path,
    playlist.matched_count,
    playlist.meta_verified_count,
    playlist.unmatched_count,
    playingTrackKey,
    specialPit,
  ]);

  const coverKey = coverCandidates.join("|");
  useEffect(() => {
    setCoverIdx(0);
  }, [coverKey]);

  const coverSrc = coverCandidates[coverIdx];
  const hasPlayable = playlist.local > 0;

  useEffect(() => {
    if (!tracksOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const root = rootRef.current;
      if (!root) return;
      const target = event.target;
      if (target instanceof Node && root.contains(target)) return;
      if (
        target instanceof Element &&
        target.closest('[role="dialog"], [aria-modal="true"]')
      ) {
        return;
      }
      onCollapseTracks();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [tracksOpen, onCollapseTracks]);

  const openSyncModal = () => {
    if (playlist.access_mode === "pending") {
      onEdit(playlist);
      return;
    }
    setSyncEnrich(true);
    setSyncRawMatch(true);
    setSyncVerifyMeta(true);
    setSyncJunkMatch(false);
    setSyncModalOpen(true);
  };

  const handleSync = async () => {
    if (syncing) return;
    if (!syncEnrich && !syncRawMatch && !syncVerifyMeta && !syncJunkMatch) {
      showErrorToast(
        t("sync.externalSyncFailed"),
        t("sync.externalSyncNeedAction"),
      );
      return;
    }
    setSyncing(true);
    setSyncModalOpen(false);
    const result = await syncExternalPlaylist(playlist.dir_name, {
      enrich: syncEnrich,
      raw_match: syncRawMatch,
      verify_meta: syncVerifyMeta,
      junk_match: syncJunkMatch,
    });
    setSyncing(false);
    if ("error" in result) {
      showErrorToast(t("sync.externalSyncFailed"), result.error);
      return;
    }
    if (result.queued) {
      showSuccessToast(
        t("sync.externalSyncQueuedTitle"),
        t("sync.externalSyncQueued"),
      );
      onChanged();
      return;
    }
    showSuccessToast(
      t("sync.externalSyncDoneTitle"),
      t("sync.externalSyncDone", {
        matched: result.matched,
        verified: result.meta_verified,
        enriched: result.enriched,
        errors: result.errors + result.asset_errors,
      }),
    );
    onChanged();
  };

  const scheduleStatus =
    playlist.access_mode === "pending"
      ? t("sync.externalPending")
      : syncing ||
          playlist.last_sync_status === "queued" ||
          playlist.last_sync_status === "running"
        ? t("sync.statusSyncing")
        : !playlist.last_synced_at && !playlist.last_sync_status
          ? t("sync.externalAwaitingScan")
          : !schedulerEnabled
            ? t("sync.statusGlobalStopped")
            : !playlist.enabled
              ? t("sync.statusStopped")
              : t("sync.statusWaiting");

  const timeLabel = playlist.last_synced_at
    ? formatDateTime(playlist.last_synced_at)
    : t("time.never");

  let resultLabel = t("sync.resultUnknown");
  if (
    syncing ||
    playlist.last_sync_status === "queued" ||
    playlist.last_sync_status === "running"
  ) {
    resultLabel = t("sync.resultRunning");
  } else if (playlist.last_sync_status === "success") {
    resultLabel = t("sync.resultSuccess");
  } else if (playlist.last_sync_status === "failed") {
    resultLabel = t("sync.resultFailed");
  } else if (playlist.last_sync_status === "interrupted") {
    resultLabel = t("sync.resultInterrupted");
  }

  const neverRun =
    !syncing && !playlist.last_synced_at && !playlist.last_sync_status;
  const historyLine = neverRun
    ? t("sync.neverSynced")
    : t("sync.historySync", { time: timeLabel, result: resultLabel });
  const historyDisplay = scheduleStatus
    ? `${scheduleStatus} · ${historyLine}`
    : historyLine;

  const counting =
    playlist.access_mode === "pending" && !playlist.inventory_scanned;
  const rejected = playlist.meta_rejected_count ?? 0;
  const pendingCount = Math.max(0, playlist.unmatched_count - rejected);
  const statsCounts = {
    total: counting
      ? t("sync.externalCounting")
      : playlist.matched_count + playlist.unmatched_count,
    cloud: playlist.cloud,
    local: playlist.local + playlist.unmatched_count,
    pending: counting ? t("sync.externalCounting") : pendingCount,
    abnormal: playlist.offline + rejected,
    exclusive: playlist.exclusive + playlist.unmatched_count,
    shared: playlist.shared,
    hardlink: playlist.hardlink,
    pendingDetails: [
      { label: t("sync.statUnmatched"), value: pendingCount },
      {
        label: t("sync.statMetaVerified"),
        value: playlist.meta_verified_count ?? 0,
      },
    ],
    abnormalDetails: [
      { label: t("sync.statOffline"), value: playlist.offline },
      { label: t("sync.statMetaRejected"), value: rejected },
    ],
  };

  return (
    <div ref={rootRef}>
      <Card shadow="sm" className="bg-content1 overflow-hidden">
        <CardBody className={SYNC_CARD_HEADER}>
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
                e.stopPropagation();
              }}
            />
          ) : null}

          {hasPlayable ? (
            <button
              type="button"
              className="group focus-visible:ring-primary relative z-20 m-3 h-14 w-14 shrink-0 overflow-hidden rounded-md outline-none focus-visible:ring-2"
              aria-label={
                isPlayingHere ? t("sync.pauseTrack") : t("sync.playTrack")
              }
              onClick={(e) => {
                e.stopPropagation();
                if (isPlayingHere) {
                  audio.togglePlaylistFolder(folder);
                  return;
                }
                void preparePlaylist().then((count) => {
                  if (count > 0) audio.togglePlaylistFolder(folder);
                });
              }}
            >
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
              {coverSrc ? (
                <img
                  src={coverSrc}
                  alt=""
                  width={56}
                  height={56}
                  className="h-14 w-14 object-cover"
                  onError={() => setCoverIdx((i) => i + 1)}
                />
              ) : (
                <div className="bg-default-100 flex h-14 w-14 items-center justify-center rounded-md">
                  {specialPit === "archive" ? (
                    <ArchiveIcon className="text-warning h-6 w-6" />
                  ) : specialPit === "deleted" ? (
                    <Trash2Icon className="text-danger h-6 w-6" />
                  ) : (
                    <FolderIcon className="text-foreground-400 h-6 w-6" />
                  )}
                </div>
              )}
            </div>
          )}

          <button
            type="button"
            className={`${SYNC_CARD_CONTENT} cursor-pointer`}
            onClick={onToggleTracks}
            aria-expanded={tracksOpen}
            aria-label={t("sync.toggleTrackList")}
          >
            <div className="max-h-full min-w-0 flex-1 overflow-hidden">
              <PlaylistTitleTooltip
                kind="external"
                dirName={playlist.dir_name}
                allowMutate={playlist.allow_mutate}
                className="text-foreground block min-w-0 truncate text-sm font-medium"
              >
                {externalPlaylistDisplayName(playlist.dir_name, t)}
              </PlaylistTitleTooltip>
              <p className={LINE}>
                <UnifiedPlaylistStats counts={statsCounts} />
              </p>
              <p className={LINE}>{historyDisplay}</p>
            </div>
          </button>

          <div className={SYNC_CARD_ACTIONS}>
            {tracksOpen && isPlayingHere ? (
              <PlaybackTransport
                folder={folder}
                isPlayingHere={isPlayingHere}
              />
            ) : null}
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={!canAdd}
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.externalAddMenu")}
              title={t("sync.externalAddMenu")}
              onPress={() => {
                setAddMode(
                  playlist.matched_count > 0
                    ? "add_matched_to_direct"
                    : "add_meta_verified_to_wanted",
                );
                setAddAllOpen(true);
              }}
            >
              <PlusIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.editExternalTitle", {
                name: externalPlaylistDisplayName(playlist.dir_name, t),
              })}
              title={t("sync.editExternalTitle", {
                name: externalPlaylistDisplayName(playlist.dir_name, t),
              })}
              onPress={() => onEdit(playlist)}
            >
              <PencilIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isLoading={syncing}
              className={`${ACTION_BTN} ${syncTone}`}
              aria-label={t("sync.syncExternal")}
              title={t("sync.syncExternal")}
              onPress={openSyncModal}
            >
              {!syncing ? <RefreshCwIcon className="h-4 w-4" /> : null}
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-danger`}
              aria-label={t("sync.deleteExternalTitle")}
              title={t("sync.deleteExternalTitle")}
              onPress={() => onDelete(playlist)}
            >
              <Trash2Icon className="h-4 w-4" />
            </Button>
          </div>
        </CardBody>
        <LedgerTrackList
          saveFolder={folder}
          open={tracksOpen}
          canDelete
          mode="external"
          externalDirName={playlist.dir_name}
          allowMutate={playlist.allow_mutate}
          showRaw={playlist.show_raw}
          showJunk={playlist.show_junk}
          onMatched={onChanged}
        />
      </Card>

      <Modal
        isOpen={syncModalOpen}
        onClose={() => {
          if (!syncing) setSyncModalOpen(false);
        }}
        placement="center"
      >
        <ModalContent>
          <ModalHeader>{t("sync.externalSyncModalTitle")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            <Checkbox
              isSelected={syncEnrich}
              onValueChange={setSyncEnrich}
              isDisabled={syncing}
            >
              <div className="flex flex-col gap-0.5">
                <span>{t("sync.externalSyncEnrich")}</span>
                <span className="text-foreground-400 text-xs font-normal">
                  {t("sync.externalSyncEnrichHint")}
                </span>
              </div>
            </Checkbox>
            <Checkbox
              isSelected={syncRawMatch}
              onValueChange={setSyncRawMatch}
              isDisabled={syncing}
            >
              <div className="flex flex-col gap-0.5">
                <span>{t("sync.externalSyncRawMatch")}</span>
                <span className="text-foreground-400 text-xs font-normal">
                  {t("sync.externalSyncRawMatchHint")}
                </span>
              </div>
            </Checkbox>
            <Checkbox
              isSelected={syncVerifyMeta}
              onValueChange={setSyncVerifyMeta}
              isDisabled={syncing}
            >
              <div className="flex flex-col gap-0.5">
                <span>{t("sync.externalSyncVerifyMeta")}</span>
                <span className="text-foreground-400 text-xs font-normal">
                  {t("sync.externalSyncVerifyMetaHint")}
                </span>
              </div>
            </Checkbox>
            <Checkbox
              isSelected={syncJunkMatch}
              onValueChange={setSyncJunkMatch}
              isDisabled={syncing}
            >
              <div className="flex flex-col gap-0.5">
                <span>{t("sync.externalSyncJunkMatch")}</span>
                <span className="text-foreground-400 text-xs font-normal">
                  {t("sync.externalSyncJunkMatchHint")}
                </span>
              </div>
            </Checkbox>
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={syncing}
              onPress={() => setSyncModalOpen(false)}
            >
              {t("sync.cancel")}
            </Button>
            <Button
              color="primary"
              isLoading={syncing}
              isDisabled={
                !syncEnrich &&
                !syncRawMatch &&
                !syncVerifyMeta &&
                !syncJunkMatch
              }
              onPress={() => {
                void handleSync();
              }}
            >
              {t("sync.syncExternal")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>

      <Modal
        isOpen={addAllOpen}
        onClose={() => {
          if (!addingAll) setAddAllOpen(false);
        }}
        placement="center"
        size="lg"
      >
        <ModalContent>
          <ModalHeader>{t("sync.externalAddMenu")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            <p className="text-foreground-400 text-xs">
              {t("sync.externalAddMenuHint")}
            </p>
            <Button
              variant={addMode === "add_matched_to_direct" ? "solid" : "flat"}
              color={
                addMode === "add_matched_to_direct" ? "primary" : "default"
              }
              className="h-auto justify-start py-3 whitespace-normal"
              isDisabled={playlist.matched_count <= 0}
              onPress={() => setAddMode("add_matched_to_direct")}
            >
              <span className="text-left">
                <span className="block font-medium">
                  {t("sync.addAllMatchedToDirect")} ({playlist.matched_count})
                </span>
                <span className="text-foreground-400 text-xs">
                  {t("sync.addAllMatchedToDirectHint")}
                </span>
              </span>
            </Button>
            <Button
              variant={
                addMode === "add_meta_verified_to_wanted" ? "solid" : "flat"
              }
              color={
                addMode === "add_meta_verified_to_wanted"
                  ? "primary"
                  : "default"
              }
              className="h-auto justify-start py-3 whitespace-normal"
              isDisabled={metaVerifiedCount <= 0}
              onPress={() => setAddMode("add_meta_verified_to_wanted")}
            >
              <span className="text-left">
                <span className="block font-medium">
                  {t("sync.addMetaVerifiedToWanted")} ({metaVerifiedCount})
                </span>
                <span className="text-foreground-400 text-xs">
                  {t("sync.addMetaVerifiedToWantedHint")}
                </span>
              </span>
            </Button>
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={addingAll}
              onPress={() => setAddAllOpen(false)}
            >
              {t("sync.cancel")}
            </Button>
            <Button
              color="primary"
              isLoading={addingAll}
              isDisabled={
                (addMode === "add_matched_to_direct" &&
                  playlist.matched_count <= 0) ||
                (addMode === "add_meta_verified_to_wanted" &&
                  metaVerifiedCount <= 0)
              }
              onPress={() => {
                setAddingAll(true);
                const titleKey =
                  addMode === "add_matched_to_direct"
                    ? "sync.addAllMatchedToDirect"
                    : "sync.addMetaVerifiedToWanted";
                const doneKey =
                  addMode === "add_matched_to_direct"
                    ? "sync.addToDirectDone"
                    : "sync.addMetaVerifiedToWantedDone";
                void deleteExternalPlaylist(playlist.dir_name, addMode).then(
                  (result) => {
                    setAddingAll(false);
                    if ("error" in result) {
                      showErrorToast(t(titleKey), result.error);
                      return;
                    }
                    setAddAllOpen(false);
                    showSuccessToast(t(titleKey), t(doneKey));
                    onChanged();
                    window.dispatchEvent(new Event("yubal:ledger-changed"));
                  },
                );
              }}
            >
              {t("sync.continue")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
