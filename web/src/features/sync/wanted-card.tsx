import { albumCoverUrl, trackCoverUrl, wantedCoverUrl } from "@/api/library";
import { getSettings } from "@/api/settings";
import type { SyncTrackItem } from "@/api/sync-ledger";
import {
  deleteWantedPlaylist,
  deleteWantedTrack,
  matchWantedTrackYtm,
  syncWanted,
  WANTED_FOLDER,
  type WantedPlaylistDeleteMode,
  type WantedSummary,
  type WantedTrackDeleteMode,
} from "@/api/wanted";
import { AudioSpectrum } from "@/features/sync/audio-spectrum";
import { PlaybackTransport } from "@/features/sync/ledger-card";
import { LedgerTrackList } from "@/features/sync/ledger-track-list";
import { useLibraryAudio } from "@/features/sync/library-audio";
import { PlaylistStatsLine } from "@/features/sync/playlist-stats-line";
import { PlaylistTitleTooltip } from "@/features/sync/playlist-title-tooltip";
import {
  SYNC_ACTION_BTN,
  SYNC_CARD_ACTIONS,
} from "@/features/sync/track-columns";
import { formatArtistTitle } from "@/features/sync/track-label";
import { WantedEditModal } from "@/features/sync/wanted-edit-modal";
import { formatDateTime } from "@/lib/format";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import {
  Button,
  Card,
  CardBody,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import {
  HeartIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  summary: WantedSummary;
  tracksOpen: boolean;
  onToggleTracks: () => void;
  onCollapseTracks: () => void;
};

const ACTION_BTN = SYNC_ACTION_BTN;
const LINE =
  "text-foreground-500 mt-1 truncate whitespace-nowrap font-mono text-xs leading-relaxed";

const PLAYLIST_MODES: WantedPlaylistDeleteMode[] = [
  "wipe_list",
  "to_raw_delete",
];

const PLAYLIST_DANGER: ReadonlySet<WantedPlaylistDeleteMode> = new Set([
  "wipe_list",
]);

export function WantedCard({
  summary,
  tracksOpen,
  onToggleTracks,
  onCollapseTracks,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const rootRef = useRef<HTMLDivElement>(null);
  const [syncing, setSyncing] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editInitial, setEditInitial] = useState<{
    auto_match_enabled: boolean;
    max_items: number;
    sync_jitter_seconds: number;
  } | null>(null);
  const [schedulerEnabled, setSchedulerEnabled] = useState(true);
  const [externalEnabled, setExternalEnabled] = useState(false);
  const [rowBusyId, setRowBusyId] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [playlistMode, setPlaylistMode] =
    useState<WantedPlaylistDeleteMode>("wipe_list");
  const [playlistBusy, setPlaylistBusy] = useState(false);
  const [deleteStep, setDeleteStep] = useState<"choose" | "confirm">("choose");
  const [pendingTrack, setPendingTrack] = useState<SyncTrackItem | null>(null);
  const [trackMode, setTrackMode] =
    useState<WantedTrackDeleteMode>("wipe_list");
  const [trackBusy, setTrackBusy] = useState(false);
  const [coverIdx, setCoverIdx] = useState(0);

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

  const isActiveFolder = audio.activeFolder === WANTED_FOLDER;
  const isPlayingHere = isActiveFolder && audio.playing;
  const playingTrackKey = isPlayingHere ? audio.key : null;
  const hasMatched = summary.matched_file_count > 0;

  const coverCandidates = useMemo(() => {
    const list: string[] = [];
    const push = (url: string | null | undefined) => {
      if (url && !list.includes(url)) list.push(url);
    };
    if (playingTrackKey) {
      push(trackCoverUrl(playingTrackKey));
      push(albumCoverUrl(playingTrackKey));
    }
    push(wantedCoverUrl(hasMatched));
    return list;
  }, [playingTrackKey, hasMatched]);

  const coverKey = coverCandidates.join("|");
  useEffect(() => {
    setCoverIdx(0);
  }, [coverKey]);
  const coverSrc = coverCandidates[coverIdx];

  const statsItems = [
    { label: t("sync.statWanted"), value: summary.total_count },
    { label: t("sync.statLocal"), value: summary.matched_file_count },
  ];

  let scheduleStatus: string;
  if (syncing) {
    scheduleStatus = t("sync.wantedStatusMatching");
  } else if (!schedulerEnabled) {
    scheduleStatus = t("sync.statusGlobalStopped");
  } else if (!summary.enabled || !summary.auto_match_enabled) {
    scheduleStatus = t("sync.wantedStatusStopped");
  } else {
    scheduleStatus = t("sync.wantedStatusWaiting");
  }

  let resultLabel = t("sync.resultUnknown");
  if (summary.last_job_status === "completed") {
    resultLabel = t("sync.resultSuccess");
  } else if (summary.last_job_status === "failed") {
    resultLabel = t("sync.resultFailed");
  }

  const timeLabel = summary.last_matched_at
    ? formatDateTime(summary.last_matched_at)
    : t("time.never");
  const neverRun = !summary.last_matched_at && !summary.last_job_status;
  const historyLine = neverRun
    ? t("sync.wantedNeverMatched")
    : t("sync.wantedHistoryMatch", {
        time: timeLabel,
        result: resultLabel,
      });
  const historyDisplay = `${scheduleStatus} · ${historyLine}`;

  const title = t("sync.wantedCardTitle");
  const headline = isPlayingHere ? audio.nowPlayingLabel || title : title;
  const statsLine = <PlaylistStatsLine items={statsItems} />;
  const subline = isPlayingHere ? (
    <>
      <span className="text-foreground">{title}</span>
      {" · "}
      {statsLine}
    </>
  ) : (
    statsLine
  );

  const refresh = () => {
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const openEdit = async () => {
    const settings = await getSettings();
    setSchedulerEnabled(settings?.scheduler_enabled ?? true);
    setEditInitial({
      auto_match_enabled:
        settings?.wanted_auto_match_enabled ?? summary.auto_match_enabled,
      max_items: settings?.wanted_max_items ?? 50,
      sync_jitter_seconds: settings?.wanted_sync_jitter_seconds ?? 600,
    });
    setEditOpen(true);
  };

  /** Same pass as the scheduler: local hardlink + YTM fulfill. */
  const handleSync = async () => {
    if (syncing) return;
    setSyncing(true);
    const result = await syncWanted();
    setSyncing(false);
    if ("error" in result) {
      showErrorToast(t("sync.syncWanted"), result.error);
      refresh();
      return;
    }
    showSuccessToast(
      t("sync.syncWanted"),
      t("sync.syncWantedDone", { linked: result.linked }),
    );
    refresh();
  };

  const handleRowMatch = async (track: SyncTrackItem) => {
    const id = track.wanted_id;
    if (!id || rowBusyId) return;
    setRowBusyId(id);
    const result = await matchWantedTrackYtm(id);
    setRowBusyId(null);
    if ("error" in result) {
      showErrorToast(t("sync.wantedMatchYtm"), result.error);
      return;
    }
    showSuccessToast(t("sync.wantedMatchYtm"), t("sync.wantedMatchYtmDone"));
    refresh();
  };

  const openTrackDelete = (track: SyncTrackItem) => {
    setPendingTrack(track);
    setTrackMode(track.relative_path ? "wipe_list" : "remove");
  };

  const confirmTrackDelete = async () => {
    if (!pendingTrack?.wanted_id || trackBusy) return;
    const mode = pendingTrack.relative_path ? trackMode : "remove";
    setTrackBusy(true);
    const ok = await deleteWantedTrack(pendingTrack.wanted_id, mode);
    setTrackBusy(false);
    if (!ok) {
      showErrorToast(
        t("sync.wantedDeleteTrackTitle"),
        t("sync.wantedDeleteFailed"),
      );
      return;
    }
    if (audio.key === pendingTrack.relative_path) audio.pause();
    setPendingTrack(null);
    refresh();
  };

  const confirmPlaylistDelete = async () => {
    if (playlistBusy) return;
    setPlaylistBusy(true);
    const result = await deleteWantedPlaylist(playlistMode);
    setPlaylistBusy(false);
    if ("error" in result) {
      showErrorToast(t("sync.wantedDeleteTitle"), result.error);
      return;
    }
    setDeleteOpen(false);
    setDeleteStep("choose");
    showSuccessToast(
      t("sync.wantedDeleteTitle"),
      t("sync.wantedDeleteDone", { count: result.removed }),
    );
    refresh();
  };

  useEffect(() => {
    void getSettings().then((settings) => {
      if (!settings) return;
      setExternalEnabled(Boolean(settings.external_library_enabled));
      setSchedulerEnabled(settings.scheduler_enabled ?? true);
    });
    const onSettings = () => {
      void getSettings().then((settings) => {
        if (!settings) return;
        setExternalEnabled(Boolean(settings.external_library_enabled));
        setSchedulerEnabled(settings.scheduler_enabled ?? true);
      });
    };
    window.addEventListener("yubal:settings-changed", onSettings);
    return () =>
      window.removeEventListener("yubal:settings-changed", onSettings);
  }, []);

  const playlistTitleKey = (mode: WantedPlaylistDeleteMode): string =>
    ({
      wipe_list: "sync.wantedDeleteMode.wipe_list",
      to_raw_delete: "sync.wantedDeleteMode.to_raw_delete",
    })[mode];

  const playlistHintKey = (mode: WantedPlaylistDeleteMode): string =>
    ({
      wipe_list: "sync.wantedDeleteModeHint.wipe_list",
      to_raw_delete: "sync.wantedDeleteModeHint.to_raw_delete",
    })[mode];

  const renderPlaylistMode = (mode: WantedPlaylistDeleteMode) => {
    if (mode === "to_raw_delete" && !externalEnabled) return null;
    return (
      <Button
        key={mode}
        variant={playlistMode === mode ? "solid" : "flat"}
        color={
          playlistMode === mode
            ? PLAYLIST_DANGER.has(mode)
              ? "danger"
              : "primary"
            : "default"
        }
        className="h-auto justify-start py-3 whitespace-normal"
        onPress={() => setPlaylistMode(mode)}
      >
        <span className="text-left">
          <span className="block font-medium">{t(playlistTitleKey(mode))}</span>
          <span className="text-foreground-400 text-xs">
            {t(playlistHintKey(mode))}
          </span>
        </span>
      </Button>
    );
  };

  const softPlaylist = playlistMode === "to_raw_delete";

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

          <button
            type="button"
            className="group focus-visible:ring-primary relative z-20 m-3 h-14 w-14 shrink-0 overflow-hidden rounded-md outline-none focus-visible:ring-2"
            aria-label={
              isPlayingHere ? t("sync.pauseTrack") : t("sync.playTrack")
            }
            onClick={(e) => {
              e.stopPropagation();
              audio.togglePlaylistFolder(WANTED_FOLDER);
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
                <HeartIcon className="text-foreground-400 h-6 w-6" />
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

          <button
            type="button"
            className="relative z-10 flex min-w-0 flex-1 cursor-pointer items-center gap-3 py-3 pr-0 text-left outline-none"
            onClick={onToggleTracks}
            aria-expanded={tracksOpen}
            aria-label={t("sync.toggleTrackList")}
          >
            <div className="min-w-0 flex-1">
              <div className="flex min-w-0 flex-nowrap items-center gap-2">
                <PlaylistTitleTooltip
                  kind="wanted"
                  className="text-foreground min-w-0 truncate text-sm font-medium"
                >
                  {headline}
                </PlaylistTitleTooltip>
              </div>
              <p className={LINE}>{subline}</p>
              <p className={LINE}>{historyDisplay}</p>
            </div>
          </button>

          <div className={SYNC_CARD_ACTIONS}>
            {isPlayingHere ? (
              <PlaybackTransport
                folder={WANTED_FOLDER}
                isPlayingHere={isPlayingHere}
              />
            ) : null}
            <Button
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.editWantedTitle")}
              title={t("sync.editWantedTitle")}
              onPress={() => {
                void openEdit();
              }}
            >
              <PencilIcon className="h-4 w-4" />
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              isLoading={syncing}
              className={`${ACTION_BTN} hover:text-primary`}
              aria-label={t("sync.syncWanted")}
              title={t("sync.syncWanted")}
              onPress={() => {
                void handleSync();
              }}
            >
              {!syncing ? <RefreshCwIcon className="h-4 w-4" /> : null}
            </Button>
            <Button
              variant="light"
              size="sm"
              isIconOnly
              className={`${ACTION_BTN} hover:text-danger`}
              aria-label={t("sync.wantedDeleteTitle")}
              title={t("sync.wantedDeleteTitle")}
              onPress={() => {
                setPlaylistMode("wipe_list");
                setDeleteStep("choose");
                setDeleteOpen(true);
              }}
            >
              <Trash2Icon className="h-4 w-4" />
            </Button>
          </div>
        </CardBody>

        <LedgerTrackList
          saveFolder={WANTED_FOLDER}
          open={tracksOpen}
          mode="wanted"
          wantedBusyId={rowBusyId}
          onWantedDelete={openTrackDelete}
          onWantedMatch={(track) => {
            void handleRowMatch(track);
          }}
        />
      </Card>

      <WantedEditModal
        isOpen={editOpen}
        initial={editInitial}
        isSchedulerEnabled={schedulerEnabled}
        onClose={() => setEditOpen(false)}
        onSaved={refresh}
      />

      <Modal
        isOpen={deleteOpen}
        onClose={() => {
          if (!playlistBusy) {
            setDeleteOpen(false);
            setDeleteStep("choose");
          }
        }}
        placement="center"
        size="lg"
      >
        <ModalContent>
          <ModalHeader>{t("sync.wantedDeleteTitle")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            {deleteStep === "choose" ? (
              <>
                <p>{t("sync.wantedDeleteBody")}</p>
                <p className="text-foreground-400 text-xs">
                  {t("sync.wantedDeleteHint")}
                </p>
                <div className="flex flex-col gap-2">
                  {PLAYLIST_MODES.map(renderPlaylistMode)}
                </div>
              </>
            ) : (
              <>
                <p>{t(`sync.wantedDeleteConfirm.${playlistMode}`)}</p>
                <p
                  className={
                    softPlaylist
                      ? "text-foreground-400 text-xs"
                      : "text-danger text-xs font-medium"
                  }
                >
                  {t("sync.clearStaleWarn")}
                </p>
              </>
            )}
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={playlistBusy}
              onPress={() => {
                if (deleteStep === "confirm") {
                  setDeleteStep("choose");
                  return;
                }
                setDeleteOpen(false);
              }}
            >
              {t("sync.cancel")}
            </Button>
            {deleteStep === "choose" ? (
              <Button
                color={PLAYLIST_DANGER.has(playlistMode) ? "danger" : "primary"}
                onPress={() => setDeleteStep("confirm")}
              >
                {t("sync.continue")}
              </Button>
            ) : (
              <Button
                color={PLAYLIST_DANGER.has(playlistMode) ? "danger" : "primary"}
                isLoading={playlistBusy}
                onPress={() => {
                  void confirmPlaylistDelete();
                }}
              >
                {t("sync.continue")}
              </Button>
            )}
          </ModalFooter>
        </ModalContent>
      </Modal>

      <Modal
        isOpen={pendingTrack !== null}
        onClose={() => {
          if (!trackBusy) setPendingTrack(null);
        }}
        placement="center"
      >
        <ModalContent>
          <ModalHeader>{t("sync.wantedDeleteTrackTitle")}</ModalHeader>
          <ModalBody className="gap-3 text-sm">
            <p>
              {t("sync.wantedDeleteTrackBody", {
                name: pendingTrack
                  ? formatArtistTitle(pendingTrack.artist, pendingTrack.title)
                  : "",
              })}
            </p>
            {pendingTrack?.relative_path ? (
              <>
                <p className="text-foreground-400 text-xs">
                  {t("sync.wantedDeleteTrackHint")}
                </p>
                <div className="flex flex-col gap-2">
                  <Button
                    variant={trackMode === "wipe_list" ? "solid" : "flat"}
                    color={trackMode === "wipe_list" ? "danger" : "default"}
                    className="h-auto justify-start py-3 whitespace-normal"
                    onPress={() => setTrackMode("wipe_list")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.wantedDeleteTrackWipeList")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {t("sync.wantedDeleteTrackWipeListHint")}
                      </span>
                    </span>
                  </Button>
                  {externalEnabled ? (
                    <Button
                      variant={trackMode === "to_raw_delete" ? "solid" : "flat"}
                      color={
                        trackMode === "to_raw_delete" ? "primary" : "default"
                      }
                      className="h-auto justify-start py-3 whitespace-normal"
                      onPress={() => setTrackMode("to_raw_delete")}
                    >
                      <span className="text-left">
                        <span className="block font-medium">
                          {t("sync.wantedDeleteTrackToRaw")}
                        </span>
                        <span className="text-foreground-400 text-xs">
                          {t("sync.wantedDeleteTrackToRawHint")}
                        </span>
                      </span>
                    </Button>
                  ) : null}
                </div>
              </>
            ) : (
              <p className="text-foreground-400 text-xs">
                {t("sync.wantedDeleteTrackRemoveHint")}
              </p>
            )}
          </ModalBody>
          <ModalFooter>
            <Button
              variant="light"
              isDisabled={trackBusy}
              onPress={() => setPendingTrack(null)}
            >
              {t("sync.cancel")}
            </Button>
            <Button
              color={
                pendingTrack?.relative_path && trackMode === "to_raw_delete"
                  ? "primary"
                  : "danger"
              }
              isLoading={trackBusy}
              onPress={() => {
                void confirmTrackDelete();
              }}
            >
              {pendingTrack?.relative_path
                ? trackMode === "to_raw_delete"
                  ? t("sync.wantedDeleteTrackToRawAction")
                  : t("sync.wantedDeleteTrackWipeListAction")
                : t("sync.wantedDeleteTrackRemoveAction")}
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </div>
  );
}
