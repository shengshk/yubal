import {
  deleteExternalPlaylist,
  listExternalPlaylistTracks,
  syncExternalPlaylist,
  type ExternalPlaylist,
  type ExternalTrack,
} from "@/api/external";
import { albumCoverUrl, playlistCoverUrl, trackCoverUrl } from "@/api/library";
import { AudioSpectrum } from "@/features/sync/audio-spectrum";
import { LedgerTrackList } from "@/features/sync/ledger-track-list";
import { useLibraryAudio } from "@/features/sync/library-audio";
import type { PlayMode } from "@/features/sync/play-mode";
import { PlaylistStatsLine } from "@/features/sync/playlist-stats-line";
import { SYNC_ACTION_BTN, SYNC_CARD_ACTIONS } from "@/features/sync/track-columns";
import { formatDateTime } from "@/lib/format";
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

function isMatchedTrack(track: ExternalTrack): boolean {
  return track.match_status === "matched" && Boolean(track.video_id) && !track.is_raw;
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
  const [syncJunkMatch, setSyncJunkMatch] = useState(false);
  const [matchedTracks, setMatchedTracks] = useState<ExternalTrack[]>([]);
  const [coverIdx, setCoverIdx] = useState(0);
  const [addAllOpen, setAddAllOpen] = useState(false);
  const [addingAll, setAddingAll] = useState(false);

  const folder = `External/${playlist.dir_name}`;
  const isPlayingHere = audio.activeFolder === folder && audio.playing;

  const loadCoverTracks = useCallback(() => {
    void listExternalPlaylistTracks(playlist.dir_name).then((items) => {
      setMatchedTracks(items.filter(isMatchedTrack));
    });
  }, [playlist.dir_name]);

  useEffect(() => {
    loadCoverTracks();
  }, [
    loadCoverTracks,
    playlist.matched_count,
    playlist.last_synced_at,
    playlist.hardlink,
  ]);

  useEffect(() => {
    const onLedgerChanged = () => loadCoverTracks();
    window.addEventListener("yubal:ledger-changed", onLedgerChanged);
    return () =>
      window.removeEventListener("yubal:ledger-changed", onLedgerChanged);
  }, [loadCoverTracks]);

  // Register playlist so cover hover play works even when the list is closed.
  useEffect(() => {
    const playable = matchedTracks.filter((tr) => tr.exists !== false);
    audio.registerPlaylist(
      folder,
      playable.map((tr) => ({
        key: tr.rel_path,
        path: tr.rel_path,
        label: [tr.artist, tr.title].filter(Boolean).join(" - ") || tr.title,
      })),
    );
  }, [audio, folder, matchedTracks]);

  const playingTrackKey =
    audio.activeFolder === folder && audio.key ? audio.key : null;

  const coverCandidates = useMemo(() => {
    const list: string[] = [];
    const push = (url: string | null | undefined) => {
      if (url && !list.includes(url)) list.push(url);
    };
    const playing = playingTrackKey
      ? matchedTracks.find((tr) => tr.rel_path === playingTrackKey)
      : null;
    const primary = playing ?? matchedTracks[0];
    const primaryPath = primary?.rel_path;

    if (playingTrackKey && primaryPath) {
      push(trackCoverUrl(playingTrackKey));
      push(albumCoverUrl(primaryPath));
      push(primary?.cover_url);
    }

    push(playlistCoverUrl(folder));

    if (primaryPath && !playingTrackKey) {
      push(albumCoverUrl(primaryPath));
      push(trackCoverUrl(primaryPath));
      push(primary?.cover_url);
    }
    return list;
  }, [folder, matchedTracks, playingTrackKey]);

  const coverKey = coverCandidates.join("|");
  useEffect(() => {
    setCoverIdx(0);
  }, [coverKey]);

  const coverSrc = coverCandidates[coverIdx];
  const hasPlayable = matchedTracks.some((tr) => tr.exists !== false);

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
    setSyncEnrich(true);
    setSyncRawMatch(true);
    setSyncJunkMatch(false);
    setSyncModalOpen(true);
  };

  const handleSync = async () => {
    if (syncing) return;
    if (!syncEnrich && !syncRawMatch && !syncJunkMatch) {
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
      junk_match: syncJunkMatch,
    });
    setSyncing(false);
    if ("error" in result) {
      showErrorToast(t("sync.externalSyncFailed"), result.error);
      return;
    }
    showSuccessToast(
      t("sync.externalSyncDoneTitle"),
      t("sync.externalSyncDone", {
        matched: result.matched,
        recovered: result.recovered,
      }),
    );
    onChanged();
    loadCoverTracks();
  };

  let scheduleStatus: string | null = null;
  if (syncing) {
    scheduleStatus = t("sync.statusSyncing");
  } else if (!schedulerEnabled) {
    scheduleStatus = t("sync.statusGlobalStopped");
  } else if (!playlist.enabled) {
    scheduleStatus = t("sync.statusStopped");
  } else {
    scheduleStatus = t("sync.statusWaiting");
  }

  const timeLabel = playlist.last_synced_at
    ? formatDateTime(playlist.last_synced_at)
    : t("time.never");

  let resultLabel = t("sync.resultUnknown");
  if (syncing) {
    resultLabel = t("sync.resultRunning");
  } else if (playlist.last_sync_status === "success") {
    resultLabel = t("sync.resultSuccess");
  } else if (playlist.last_sync_status === "failed") {
    resultLabel = t("sync.resultFailed");
  } else if (playlist.last_sync_status === "interrupted") {
    resultLabel = t("sync.resultInterrupted");
  }

  const neverRun = !syncing && !playlist.last_synced_at && !playlist.last_sync_status;
  const historyLine = neverRun
    ? t("sync.neverSynced")
    : t("sync.historySync", { time: timeLabel, result: resultLabel });
  const historyDisplay = scheduleStatus
    ? `${scheduleStatus} · ${historyLine}`
    : historyLine;

  const statsItems = [
    { label: t("sync.statUnmatched"), value: playlist.unmatched_count },
    { label: t("sync.statMatched"), value: playlist.matched_count },
    { label: t("sync.statCloud"), value: playlist.cloud },
    { label: t("sync.statLocal"), value: playlist.local },
    { label: t("sync.statIdInvalid"), value: playlist.offline },
    { label: t("sync.statExclusive"), value: playlist.exclusive },
    { label: t("sync.statShared"), value: playlist.shared },
    { label: t("sync.statHardlink"), value: playlist.hardlink },
  ];

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
                audio.togglePlaylistFolder(folder);
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
              <div className="bg-default-100 flex h-14 w-14 items-center justify-center rounded-md">
                <FolderIcon className="text-foreground-400 h-6 w-6" />
              </div>
            </div>
          )}

          <button
            type="button"
            className="relative z-10 flex min-w-0 flex-1 cursor-pointer items-center gap-3 py-3 pr-0 text-left outline-none"
            onClick={onToggleTracks}
            aria-expanded={tracksOpen}
            aria-label={t("sync.toggleTrackList")}
          >
            <div className="min-w-0 flex-1">
              <p className="text-foreground min-w-0 truncate text-sm font-medium">
                {playlist.dir_name}
              </p>
              <p className={LINE}>
                <PlaylistStatsLine
                  items={statsItems}
                  trailing={
                    !playlist.allow_mutate ? (
                      <span className="text-warning">
                        <span className="text-foreground-400 px-1" aria-hidden>
                          ·
                        </span>
                        {t("sync.externalImmutable")}
                      </span>
                    ) : null
                  }
                />
              </p>
              <p className={LINE}>{historyDisplay}</p>
            </div>
          </button>

          <div className={SYNC_CARD_ACTIONS}>
            {tracksOpen && isPlayingHere ? (
              <PlaybackTransport folder={folder} isPlayingHere={isPlayingHere} />
            ) : null}
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isDisabled={playlist.matched_count <= 0}
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.addAllMatchedToDirect")}
              title={t("sync.addAllMatchedToDirect")}
              onPress={() => setAddAllOpen(true)}
            >
              <PlusIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.editExternalTitle", {
                name: playlist.dir_name,
              })}
              title={t("sync.editExternalTitle", { name: playlist.dir_name })}
              onPress={() => onEdit(playlist)}
            >
              <PencilIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isLoading={syncing}
              className={`${ACTION_BTN} hover:text-primary`}
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
              isDisabled={!syncEnrich && !syncRawMatch && !syncJunkMatch}
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
      >
        <ModalContent>
          <ModalHeader>{t("sync.addAllMatchedToDirect")}</ModalHeader>
          <ModalBody className="gap-2 text-sm">
            <p>
              {t("sync.addAllMatchedToDirectBody", {
                name: playlist.dir_name,
                count: playlist.matched_count,
              })}
            </p>
            <p className="text-foreground-400 text-xs">
              {t("sync.addAllMatchedToDirectHint")}
            </p>
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
              onPress={() => {
                setAddingAll(true);
                void deleteExternalPlaylist(
                  playlist.dir_name,
                  "add_matched_to_direct",
                ).then((result) => {
                  setAddingAll(false);
                  if ("error" in result) {
                    showErrorToast(
                      t("sync.addAllMatchedToDirect"),
                      result.error,
                    );
                    return;
                  }
                  setAddAllOpen(false);
                  showSuccessToast(
                    t("sync.addAllMatchedToDirect"),
                    t("sync.addToDirectDone"),
                  );
                  onChanged();
                  window.dispatchEvent(new Event("yubal:ledger-changed"));
                });
              }}
            >
              {t("sync.addToDirectAction")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
