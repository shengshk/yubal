import {
  importSearchPreview,
  prepareSearchPreview,
  searchPreviewUrl,
  type SearchSnapshot,
  type SearchTrack,
} from "@/api/search";
import {
  addWantedTrack,
  deleteWantedTrack,
  listWantedTracks,
} from "@/api/wanted";
import {
  listSubscriptionTracks,
  listSubscriptions,
  rateLikedSong,
} from "@/api/subscriptions";
import { trackCoverUrl } from "@/api/library";
import {
  enrichTrack,
  listSyncTracks,
  type SyncTrackItem,
} from "@/api/sync-ledger";
import { AudioSpectrum } from "@/features/sync/audio-spectrum";
import { useLibraryAudio } from "@/features/sync/library-audio";
import { FavoriteAction } from "@/features/sync/favorite-action";
import type { PlayMode } from "@/features/sync/play-mode";
import { PlaylistStatsLine } from "@/features/sync/playlist-stats-line";
import { PlaylistTitleTooltip } from "@/features/sync/playlist-title-tooltip";
import {
  SYNC_ACTION_BTN,
  SYNC_CARD_ACTIONS,
  SYNC_CARD_CONTENT,
  SYNC_CARD_HEADER,
  TRACK_ACTIONS,
  TRACK_ACTION_SLOT,
  TRACK_INDEX,
  TRACK_INDEX_ICON,
  TRACK_ROW_GRID,
  TrackActionSlot,
  TrackTextCells,
} from "@/features/sync/track-columns";
import { formatArtistTitle } from "@/features/sync/track-label";
import { TrackEditModal } from "@/features/sync/track-edit-modal";
import { formatDateTime } from "@/lib/format";
import { isLikedMusicUrl } from "@/lib/subscription-labels";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import { layout } from "@/lib/ui-styles";
import { Button, Card, CardBody, Spinner } from "@heroui/react";
import {
  AudioLinesIcon,
  CaptionsIcon,
  DownloadIcon,
  ExternalLinkIcon,
  ListMusicIcon,
  ListOrderedIcon,
  PauseIcon,
  PencilIcon,
  PlayIcon,
  Repeat1Icon,
  RepeatIcon,
  SearchIcon,
  ShuffleIcon,
  SkipBackIcon,
  SkipForwardIcon,
  SparklesIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export const SEARCH_FOLDER = "__search__";
const ACTION_BTN = SYNC_ACTION_BTN;
const LINE = "text-foreground-500 mt-1 truncate font-mono text-xs";

type Props = {
  snapshot: SearchSnapshot;
  tracksOpen: boolean;
  onToggleTracks: () => void;
  onCollapseTracks: () => void;
  onSnapshotChange: (snapshot: SearchSnapshot) => void;
  onDelete: () => Promise<void>;
  onDownload: (track: SearchTrack) => Promise<void>;
  onImported: () => void;
  onExpired: () => void;
};

function isMetaTrack(track: SearchTrack): boolean {
  return Boolean(track.wishable) || track.result_kind === "meta";
}

function labelFor(track: SearchTrack): string {
  return formatArtistTitle(track.artist, track.title);
}

function trackKey(track: SearchTrack): string {
  if (track.matched && track.local_path) return track.local_path;
  if (track.video_id) return `search-preview:${track.video_id}`;
  return `search-meta:${track.source ?? "meta"}:${track.source_id ?? track.rank}`;
}

function trackIdentity(track: SearchTrack): string {
  if (track.video_id) return track.video_id;
  return `${track.source ?? "meta"}:${track.source_id ?? ""}:${track.rank}`;
}

/** Soft match key aligned with WantedService.add() title/artist/album dedupe. */
function wantedSoftKey(
  title: string,
  artists: string,
  album?: string | null,
): string {
  return `${title.trim().toLowerCase()}|${artists.trim().toLowerCase()}|${(album ?? "").trim().toLowerCase()}`;
}

function searchWantedKeys(track: SearchTrack): string[] {
  const keys = [wantedSoftKey(track.title, track.artist, track.album)];
  const sourceId = (track.source_id || track.video_id || "").trim();
  if (sourceId) keys.push(`sid:${sourceId}`);
  return keys;
}

function splitLocalPath(localPath: string): {
  saveFolder: string;
  relativePath: string;
} {
  const normalized = localPath.replace(/^\/+/, "");
  const separator = normalized.indexOf("/");
  return {
    saveFolder: separator >= 0 ? normalized.slice(0, separator) : normalized,
    relativePath: separator >= 0 ? normalized.slice(separator + 1) : normalized,
  };
}

function PlayModeIcon({ mode }: { mode: PlayMode }) {
  const cls = "h-4 w-4";
  switch (mode) {
    case "loop":
      return <RepeatIcon className={cls} />;
    case "single_loop":
      return <Repeat1Icon className={cls} />;
    case "shuffle":
      return <ShuffleIcon className={cls} />;
    case "single":
      return <ListOrderedIcon className={cls} />;
    default:
      return <ListMusicIcon className={cls} />;
  }
}

export function SearchResultsCard({
  snapshot,
  tracksOpen,
  onToggleTracks,
  onCollapseTracks,
  onSnapshotChange,
  onDelete,
  onDownload,
  onImported,
  onExpired,
}: Props) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const [deleting, setDeleting] = useState(false);
  const [previewing, setPreviewing] = useState<string | null>(null);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [loadingEdit, setLoadingEdit] = useState<string | null>(null);
  const [enriching, setEnriching] = useState<string | null>(null);
  const [localMatches, setLocalMatches] = useState<
    Record<string, { saveFolder: string; track: SyncTrackItem }>
  >({});
  const [pendingEdit, setPendingEdit] = useState<{
    track: SyncTrackItem;
    saveFolder: string;
  } | null>(null);
  const [previewUrls, setPreviewUrls] = useState<Record<string, string>>({});
  const [coverIdx, setCoverIdx] = useState(0);
  const [addingWanted, setAddingWanted] = useState<string | null>(null);
  const [wantedKeys, setWantedKeys] = useState<Set<string>>(new Set());
  const [wantedTrackIds, setWantedTrackIds] = useState<Map<string, string>>(
    new Map(),
  );
  const [likedSubscriptionId, setLikedSubscriptionId] = useState<string | null>(
    null,
  );
  const [likedVideoIds, setLikedVideoIds] = useState<Set<string>>(new Set());
  const [ratingVideoId, setRatingVideoId] = useState<string | null>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const latestAudio = useRef(audio);

  useEffect(() => {
    latestAudio.current = audio;
  }, [audio]);

  useEffect(() => {
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
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadLiked = async () => {
      const liked = (await listSubscriptions()).find((sub) =>
        isLikedMusicUrl(sub.url),
      );
      if (cancelled) return;
      setLikedSubscriptionId(liked?.id ?? null);
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
  }, []);

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
      onCollapseTracks();
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [tracksOpen, onCollapseTracks]);

  useEffect(
    () => () => {
      if (latestAudio.current.activeFolder === SEARCH_FOLDER) {
        latestAudio.current.pause();
      }
    },
    [],
  );

  useEffect(() => {
    let timer: number | undefined;
    const schedule = () => {
      const remaining = new Date(snapshot.expires_at).getTime() - Date.now();
      if (remaining <= 0) {
        onExpired();
        return;
      }
      timer = window.setTimeout(schedule, Math.min(remaining, 2_147_000_000));
    };
    schedule();
    return () => {
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [snapshot.expires_at, onExpired]);

  useEffect(() => {
    if (!tracksOpen) return;
    let cancelled = false;
    const matched = snapshot.tracks.filter(
      (track): track is SearchTrack & { local_path: string } =>
        track.matched && Boolean(track.local_path),
    );
    const folders = [
      ...new Set(
        matched.map((track) => splitLocalPath(track.local_path).saveFolder),
      ),
    ];
    void Promise.all(
      folders.map(async (saveFolder) => ({
        saveFolder,
        tracks: await listSyncTracks(saveFolder),
      })),
    ).then((groups) => {
      if (cancelled) return;
      const next: Record<string, { saveFolder: string; track: SyncTrackItem }> =
        {};
      for (const matchedTrack of matched) {
        const { saveFolder, relativePath } = splitLocalPath(
          matchedTrack.local_path,
        );
        const group = groups.find((item) => item.saveFolder === saveFolder);
        const local =
          group?.tracks.find(
            (item) => item.video_id === matchedTrack.video_id,
          ) ??
          group?.tracks.find((item) => item.relative_path === relativePath);
        if (local) {
          next[matchedTrack.video_id] = { saveFolder, track: local };
        }
      }
      setLocalMatches(next);
    });
    return () => {
      cancelled = true;
    };
  }, [snapshot.tracks, tracksOpen]);

  const numbered = useMemo(() => {
    const localIds = new Map<string, string>();
    const onlineIds = new Map<string, string>();
    let localN = 0;
    let onlineN = 0;
    for (const track of [...snapshot.tracks].sort((a, b) => a.rank - b.rank)) {
      const id = trackIdentity(track);
      if (track.matched) {
        localIds.set(id, `L${++localN}`);
      } else if (!isMetaTrack(track)) {
        onlineIds.set(id, String(++onlineN));
      }
    }
    return { localIds, onlineIds };
  }, [snapshot.tracks]);

  const displayTracks = useMemo(() => {
    return [...snapshot.tracks].sort((a, b) => {
      const aPlaying =
        audio.activeFolder === SEARCH_FOLDER && audio.key === trackKey(a);
      const bPlaying =
        audio.activeFolder === SEARCH_FOLDER && audio.key === trackKey(b);
      const rankOf = (track: SearchTrack, playing: boolean) => {
        if (playing) return 0;
        if (track.matched) return 1;
        if (isMetaTrack(track)) return 4;
        if (track.preview_cached || previewUrls[track.video_id]) return 2;
        return 3;
      };
      return rankOf(a, aPlaying) - rankOf(b, bPlaying) || a.rank - b.rank;
    });
  }, [snapshot.tracks, audio.activeFolder, audio.key, previewUrls]);

  useEffect(() => {
    latestAudio.current.registerPlaylist(
      SEARCH_FOLDER,
      displayTracks.flatMap((track) => {
        if (isMetaTrack(track)) return [];
        const label = labelFor(track);
        if (track.matched && track.local_path) {
          return [
            {
              key: track.local_path,
              path: track.local_path,
              label,
            },
          ];
        }
        const url =
          previewUrls[track.video_id] ??
          (track.preview_cached ? searchPreviewUrl(track.video_id) : null);
        if (!url || !track.video_id) return [];
        return [
          {
            key: `search-preview:${track.video_id}`,
            path: track.video_id,
            label,
            sourceUrl: url,
          },
        ];
      }),
    );
  }, [displayTracks, previewUrls]);

  const isActiveFolder = audio.activeFolder === SEARCH_FOLDER;
  const isPlayingHere = isActiveFolder && audio.playing;
  const showTransport = isPlayingHere;
  const mode = audio.getPlayModeFor(SEARCH_FOLDER);
  const lyricsEnabled = isPlayingHere && audio.lyricsAvailable;
  const lyricsShown = audio.lyricsHeaderVisible;

  const playingTrack = useMemo(() => {
    if (!isPlayingHere || !audio.key) return null;
    return (
      snapshot.tracks.find((track) => trackKey(track) === audio.key) ?? null
    );
  }, [isPlayingHere, audio.key, snapshot.tracks]);

  const defaultCoverUrl = useMemo(
    () =>
      [...snapshot.tracks]
        .sort((a, b) => a.rank - b.rank)
        .find((track) => track.thumbnail_url)?.thumbnail_url ?? null,
    [snapshot.tracks],
  );

  // Ordered cover candidates; a dead URL falls through to the next, and
  // finally to the search icon — so a broken thumbnail never leaves a hole.
  const coverCandidates = useMemo(() => {
    const list: string[] = [];
    const push = (url: string | null | undefined) => {
      if (url && !list.includes(url)) list.push(url);
    };
    if (isPlayingHere && playingTrack) {
      if (playingTrack.matched && playingTrack.local_path) {
        push(trackCoverUrl(playingTrack.local_path));
      }
      push(playingTrack.thumbnail_url);
    }
    push(defaultCoverUrl);
    return list;
  }, [isPlayingHere, playingTrack, defaultCoverUrl]);

  const coverKey = coverCandidates.join("|");
  useEffect(() => {
    setCoverIdx(0);
  }, [coverKey]);

  const coverSrc = coverCandidates[coverIdx];

  const cachedCount = useMemo(
    () =>
      snapshot.tracks.filter(
        (track) =>
          !track.matched &&
          !isMetaTrack(track) &&
          (track.preview_cached || Boolean(previewUrls[track.video_id])),
      ).length,
    [snapshot.tracks, previewUrls],
  );

  const headline = isPlayingHere
    ? audio.nowPlayingLabel || t("search.cardTitle")
    : t("search.cardTitle");
  const ytmCount = snapshot.tracks.filter(
    (track) => !isMetaTrack(track),
  ).length;
  const metadataCount = snapshot.tracks.length - ytmCount;
  const statsLine = (
    <PlaylistStatsLine
      items={[
        { label: t("search.statsTotal"), value: snapshot.total_count },
        { label: t("search.statsYtm"), value: ytmCount },
        { label: t("search.statsMetadata"), value: metadataCount },
        { label: t("search.statsLocal"), value: snapshot.matched_count },
        { label: t("search.statsCached"), value: cachedCount },
      ]}
    />
  );
  const subline = isPlayingHere ? (
    <>
      <span className="text-foreground">{t("search.cardTitle")}</span>
      <span className="text-foreground-400 px-2" aria-hidden>
        ·
      </span>
      {statsLine}
    </>
  ) : (
    statsLine
  );

  const playTrack = async (track: SearchTrack) => {
    if (isMetaTrack(track) || !track.video_id) return;
    const label = labelFor(track);
    if (track.matched && track.local_path) {
      audio.play(track.local_path, track.local_path, SEARCH_FOLDER);
      return;
    }
    const key = `search-preview:${track.video_id}`;
    const cachedUrl =
      previewUrls[track.video_id] ??
      (track.preview_cached ? searchPreviewUrl(track.video_id) : null);
    if (cachedUrl) {
      setPreviewUrls((current) =>
        current[track.video_id]
          ? current
          : { ...current, [track.video_id]: cachedUrl },
      );
      audio.playUrl(key, cachedUrl, SEARCH_FOLDER, label, track.thumbnail_url);
      return;
    }
    if (previewing) return;
    setPreviewing(track.video_id);
    const result = await prepareSearchPreview(track.video_id);
    setPreviewing(null);
    if ("error" in result) {
      showErrorToast(t("search.previewFailed"), result.error);
      return;
    }
    const resolvedUrl = result.data.url;
    setPreviewUrls((current) => ({
      ...current,
      [track.video_id]: resolvedUrl,
    }));
    onSnapshotChange({
      ...snapshot,
      cached_count: snapshot.tracks.filter(
        (item) =>
          !item.matched &&
          !isMetaTrack(item) &&
          (item.video_id === track.video_id ||
            item.preview_cached ||
            Boolean(previewUrls[item.video_id])),
      ).length,
      tracks: snapshot.tracks.map((item) =>
        item.video_id === track.video_id
          ? { ...item, preview_cached: true }
          : item,
      ),
    });
    audio.playUrl(key, resolvedUrl, SEARCH_FOLDER, label, track.thumbnail_url);
  };

  const onRowClick = (
    event: React.MouseEvent<HTMLElement>,
    track: SearchTrack,
  ) => {
    if (isMetaTrack(track) || !track.video_id) return;
    const key = trackKey(track);
    const isCurrent = audio.activeFolder === SEARCH_FOLDER && audio.key === key;
    if (!isCurrent) {
      void playTrack(track);
      return;
    }
    const row = event.currentTarget.closest("li");
    const rect = row?.getBoundingClientRect();
    if (!rect) return;
    const ratio = (event.clientX - rect.left) / Math.max(1, rect.width);
    audio.seek(ratio);
  };

  const remove = async () => {
    if (deleting) return;
    setDeleting(true);
    if (audio.activeFolder === SEARCH_FOLDER) audio.pause();
    await onDelete();
    setDeleting(false);
  };

  const handleDownload = async (track: SearchTrack) => {
    if (track.matched || downloading) return;
    setDownloading(track.video_id);
    try {
      if (track.preview_cached || previewUrls[track.video_id]) {
        const result = await importSearchPreview(track.video_id);
        if ("error" in result) {
          showErrorToast(t("search.importFailed"), result.error);
          return;
        }
        onSnapshotChange(result.data.snapshot);
        onImported();
        return;
      }
      await onDownload(track);
    } finally {
      setDownloading(null);
    }
  };

  const openMatchedEditor = async (track: SearchTrack) => {
    if (!track.matched || !track.local_path || loadingEdit) return;
    setLoadingEdit(track.video_id);
    const { saveFolder, relativePath } = splitLocalPath(track.local_path);
    const cached = localMatches[track.video_id];
    const localTracks = cached ? [] : await listSyncTracks(saveFolder);
    const local =
      cached?.track ??
      localTracks.find((item) => item.video_id === track.video_id) ??
      localTracks.find((item) => item.relative_path === relativePath);
    setLoadingEdit(null);
    setPendingEdit({
      saveFolder,
      track: local ?? {
        index: track.rank,
        title: track.title,
        artist: track.artist,
        album_artist: track.artist,
        album: track.album ?? "",
        exists: true,
        storage: "real",
        relative_path: relativePath,
        video_id: track.video_id,
        cover_url: track.thumbnail_url,
      },
    });
  };

  const handleEnrichTrack = async (videoId: string) => {
    const local = localMatches[videoId];
    if (!local || enriching || local.track.tier === "premium") return;
    setEnriching(videoId);
    const summary = await enrichTrack(videoId);
    setEnriching(null);
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
    } else {
      showSuccessToast(
        t("sync.enrichTrackDoneTitle"),
        summary.upgraded > 0
          ? t("sync.enrichTrackUpgraded")
          : t("sync.enrichTrackUnchanged"),
      );
    }
    const refreshed = await listSyncTracks(local.saveFolder);
    const updated = refreshed.find((item) => item.video_id === videoId);
    if (updated) {
      setLocalMatches((current) => ({
        ...current,
        [videoId]: { saveFolder: local.saveFolder, track: updated },
      }));
    }
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const handleAddWanted = async (track: SearchTrack) => {
    const id = trackIdentity(track);
    if (addingWanted) return;
    if (searchWantedKeys(track).some((key) => wantedKeys.has(key))) return;
    setAddingWanted(id);
    const result = await addWantedTrack({
      title: track.title,
      artists: track.artist,
      album: track.album ?? "",
      source: track.source || track.result_kind || "meta",
      source_id: track.source_id || track.video_id || "",
      source_url: track.source_url ?? null,
      thumbnail_url: track.thumbnail_url,
      duration_seconds: track.duration_seconds,
    });
    setAddingWanted(null);
    if ("error" in result) {
      showErrorToast(t("search.addToWanted"), result.error);
      return;
    }
    setWantedKeys((current) => {
      const next = new Set(current);
      for (const key of searchWantedKeys(track)) next.add(key);
      const row = result.data;
      next.add(wantedSoftKey(row.title, row.artists, row.album));
      const sid = (row.source_id || row.video_id || "").trim();
      if (sid) next.add(`sid:${sid}`);
      return next;
    });
    showSuccessToast(t("search.addToWanted"), t("search.addedToWanted"));
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const toggleLocalHeart = async (track: SearchTrack) => {
    const wantedId = searchWantedKeys(track)
      .map((key) => wantedTrackIds.get(key))
      .find(Boolean);
    if (!wantedId) {
      await handleAddWanted(track);
      return;
    }
    if (addingWanted) return;
    setAddingWanted(trackIdentity(track));
    const ok = await deleteWantedTrack(wantedId, "remove");
    setAddingWanted(null);
    if (!ok) {
      showErrorToast(t("sync.removeLocalHeart"), t("sync.favoriteActionFailed"));
      return;
    }
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  const toggleRemoteLike = async (track: SearchTrack) => {
    if (!likedSubscriptionId || !track.video_id || ratingVideoId) return;
    const liked = likedVideoIds.has(track.video_id);
    setRatingVideoId(track.video_id);
    const result = await rateLikedSong(likedSubscriptionId, track.video_id, !liked);
    setRatingVideoId(null);
    if (!result.success) {
      showErrorToast(liked ? t("sync.unlikeYtm") : t("sync.likeYtm"), result.error);
      return;
    }
    setLikedVideoIds((current) => {
      const next = new Set(current);
      if (liked) next.delete(track.video_id);
      else next.add(track.video_id);
      return next;
    });
    showSuccessToast(
      liked ? t("sync.unlikeYtm") : t("sync.likeYtm"),
      liked ? t("sync.unlikeYtmQueued") : t("sync.likeYtmQueued"),
    );
    window.dispatchEvent(new Event("yubal:ledger-changed"));
  };

  return (
    <section className={`flex flex-col ${layout.sectionInner}`}>
      <h2 className={layout.sectionTitle}>
        <PlaylistTitleTooltip kind="search">
          {t("search.sectionTitle")}
        </PlaylistTitleTooltip>
      </h2>
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
                  const ratio =
                    (e.clientX - rect.left) / Math.max(1, rect.width);
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
                if (isActiveFolder && audio.key) {
                  audio.togglePlaylistFolder(SEARCH_FOLDER);
                  return;
                }
                const first = displayTracks.find(
                  (track) => !isMetaTrack(track),
                );
                if (first) void playTrack(first);
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
                  <SearchIcon className="text-foreground-400 h-6 w-6" />
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
              className={`${SYNC_CARD_CONTENT} cursor-pointer`}
              onClick={onToggleTracks}
              aria-expanded={tracksOpen}
            >
              <div className="max-h-full min-w-0 flex-1 overflow-hidden">
                <PlaylistTitleTooltip
                  kind="search"
                  className="text-foreground block min-w-0 truncate text-sm font-medium"
                >
                  {headline}
                </PlaylistTitleTooltip>
                <p className={LINE}>{subline}</p>
                <p className={LINE}>
                  {t("search.lastSearched", {
                    time: formatDateTime(snapshot.searched_at),
                  })}
                </p>
              </div>
            </button>

            <div className={SYNC_CARD_ACTIONS}>
              {showTransport ? (
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
                    aria-label={
                      isPlayingHere ? t("sync.pauseTrack") : t("sync.playTrack")
                    }
                    onPress={() => audio.togglePlaylistFolder(SEARCH_FOLDER)}
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
                    onPress={() => audio.cyclePlayModeFor(SEARCH_FOLDER)}
                  >
                    <PlayModeIcon mode={mode} />
                  </Button>
                </>
              ) : null}
              <Button
                variant="light"
                size="sm"
                isIconOnly
                isLoading={deleting}
                className={`${ACTION_BTN} hover:text-danger`}
                aria-label={t("search.delete")}
                onPress={() => {
                  void remove();
                }}
              >
                <Trash2Icon className="h-4 w-4" />
              </Button>
            </div>
          </CardBody>

          {tracksOpen ? (
            <div className="border-default-200 bg-content2/40 max-h-[50rem] overflow-y-auto overscroll-contain border-t">
              <ul className="divide-default-100 divide-y">
                {displayTracks.map((track) => {
                  const id = trackIdentity(track);
                  const meta = isMetaTrack(track);
                  const current =
                    !meta &&
                    audio.activeFolder === SEARCH_FOLDER &&
                    audio.key === trackKey(track);
                  const playing = current && audio.playing;
                  const busyPreview = previewing === track.video_id;
                  const busyDownload = downloading === track.video_id;
                  const busyWanted = addingWanted === id;
                  const inWanted = searchWantedKeys(track).some((key) =>
                    wantedKeys.has(key),
                  );
                  const cached =
                    !meta &&
                    !track.matched &&
                    (track.preview_cached ||
                      Boolean(previewUrls[track.video_id]));
                  const displayIndex = track.matched
                    ? (numbered.localIds.get(id) ?? "L?")
                    : meta
                      ? ""
                      : (numbered.onlineIds.get(id) ?? "?");
                  const downloadClass = track.matched
                    ? `${ACTION_BTN} text-success opacity-100`
                    : cached
                      ? `${ACTION_BTN} text-warning hover:text-warning`
                      : `${ACTION_BTN} hover:text-primary`;
                  const local = track.video_id
                    ? localMatches[track.video_id]?.track
                    : undefined;
                  const enrichLabel =
                    local?.tier === "premium"
                      ? t("sync.tierOptimal")
                      : local?.tier === "draft"
                        ? t("sync.fillCover")
                        : local?.has_synced_lyrics
                          ? t("sync.upgradeCover")
                          : t("sync.upgradeLyrics");
                  const enrichClass =
                    local?.tier === "premium"
                      ? "!text-success-400/80 data-[disabled=true]:opacity-100"
                      : local?.tier === "draft"
                        ? "!text-warning-400/90 hover:!text-warning-500"
                        : "hover:text-primary";
                  const rowLabel = labelFor(track);
                  return (
                    <li key={id} className="relative h-8 overflow-hidden">
                      <div
                        className={`${TRACK_ROW_GRID} ${
                          meta
                            ? "text-foreground-400"
                            : "text-foreground cursor-pointer"
                        }`}
                        role={meta ? undefined : "button"}
                        tabIndex={meta ? undefined : 0}
                        onClick={
                          meta
                            ? undefined
                            : (event) => {
                                onRowClick(event, track);
                              }
                        }
                        onKeyDown={
                          meta
                            ? undefined
                            : (event) => {
                                if (
                                  event.key === "Enter" ||
                                  event.key === " "
                                ) {
                                  event.preventDefault();
                                  onRowClick(event as never, track);
                                }
                              }
                        }
                        aria-label={
                          meta
                            ? rowLabel
                            : current
                              ? t("sync.seekTrack")
                              : `${t("sync.playTrack")}: ${rowLabel}`
                        }
                        title={rowLabel}
                      >
                        <span className={TRACK_INDEX}>
                          <span className={TRACK_INDEX_ICON} aria-hidden>
                            {playing ? (
                              <AudioLinesIcon className="text-primary h-3 w-3" />
                            ) : busyPreview ? (
                              <Spinner size="sm" className="scale-75" />
                            ) : null}
                          </span>
                          <span className="min-w-[1.25rem] text-right">
                            {displayIndex}
                          </span>
                        </span>
                        <TrackTextCells
                          title={track.title}
                          artist={track.artist}
                          album={local?.album ?? track.album}
                          albumArtist={local?.album_artist}
                        />
                        <div
                          className={TRACK_ACTIONS}
                          onClick={(event) => event.stopPropagation()}
                          onKeyDown={(event) => event.stopPropagation()}
                        >
                          <span className={TRACK_ACTION_SLOT}>
                            {meta ? (
                              <FavoriteAction
                                kind="local"
                                active={inWanted}
                                busy={busyWanted}
                                className={ACTION_BTN}
                                onPress={() => {
                                  void toggleLocalHeart(track);
                                }}
                              />
                            ) : likedSubscriptionId ? (
                              <FavoriteAction
                                kind="remote"
                                active={likedVideoIds.has(track.video_id)}
                                busy={ratingVideoId === track.video_id}
                                className={ACTION_BTN}
                                onPress={() => {
                                  void toggleRemoteLike(track);
                                }}
                              />
                            ) : (
                              <FavoriteAction
                                kind="remote"
                                active={false}
                                disabled
                                className={ACTION_BTN}
                              />
                            )}
                          </span>
                          <TrackActionSlot
                            fallbackIcon={
                              <ExternalLinkIcon className="h-3.5 w-3.5" />
                            }
                            fallbackLabel={t("sync.openInYtm")}
                          >
                            {meta && track.source_url ? (
                              <Button
                                as="a"
                                href={track.source_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                variant="light"
                                size="sm"
                                isIconOnly
                                className={`${ACTION_BTN} hover:text-primary`}
                                aria-label={t("search.openSource")}
                                title={t("search.openSource")}
                              >
                                <ExternalLinkIcon className="h-3.5 w-3.5" />
                              </Button>
                            ) : !meta && track.video_id ? (
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
                            fallbackLabel={t("search.download")}
                          >
                            {!meta ? (
                              <Button
                                variant="light"
                                size="sm"
                                isIconOnly
                                isLoading={busyDownload}
                                isDisabled={
                                  track.matched ||
                                  (Boolean(downloading) && !busyDownload)
                                }
                                className={downloadClass}
                                aria-label={
                                  track.matched
                                    ? t("search.downloaded")
                                    : cached
                                      ? t("search.importCached")
                                      : t("search.download")
                                }
                                title={
                                  track.matched
                                    ? t("search.downloaded")
                                    : cached
                                      ? t("search.importCached")
                                      : t("search.download")
                                }
                                onPress={() => {
                                  void handleDownload(track);
                                }}
                              >
                                <DownloadIcon className="h-3.5 w-3.5" />
                              </Button>
                            ) : null}
                          </TrackActionSlot>
                          <TrackActionSlot
                            fallbackIcon={<PencilIcon className="h-3.5 w-3.5" />}
                            fallbackLabel={t("sync.editTrackTags")}
                          >
                            {!meta && track.matched && track.local_path ? (
                              <Button
                                variant="light"
                                size="sm"
                                isIconOnly
                                isLoading={loadingEdit === track.video_id}
                                isDisabled={
                                  Boolean(loadingEdit) &&
                                  loadingEdit !== track.video_id
                                }
                                className={`${ACTION_BTN} hover:text-primary`}
                                aria-label={t("sync.editTrackTags")}
                                title={t("sync.editTrackTags")}
                                onPress={() => {
                                  void openMatchedEditor(track);
                                }}
                              >
                                <PencilIcon className="h-3.5 w-3.5" />
                              </Button>
                            ) : null}
                          </TrackActionSlot>
                          <TrackActionSlot
                            fallbackIcon={<SparklesIcon className="h-3.5 w-3.5" />}
                            fallbackLabel={t("sync.matchTrack")}
                          >
                            {!meta && local ? (
                              <Button
                                variant="light"
                                size="sm"
                                isIconOnly
                                isLoading={enriching === track.video_id}
                                isDisabled={
                                  local.tier === "premium" ||
                                  (Boolean(enriching) &&
                                    enriching !== track.video_id)
                                }
                                className={`${ACTION_BTN} ${enrichClass}`}
                                aria-label={enrichLabel}
                                title={enrichLabel}
                                onPress={() => {
                                  void handleEnrichTrack(track.video_id);
                                }}
                              >
                                <SparklesIcon className="h-3.5 w-3.5" />
                              </Button>
                            ) : null}
                          </TrackActionSlot>
                          <TrackActionSlot
                            fallbackIcon={<Trash2Icon className="h-3.5 w-3.5" />}
                            fallbackLabel={t("sync.deleteTrack")}
                          />
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </div>
          ) : null}
        </Card>

        <TrackEditModal
          track={pendingEdit?.track ?? null}
          saveFolder={pendingEdit?.saveFolder ?? ""}
          isOpen={pendingEdit !== null}
          onClose={() => setPendingEdit(null)}
          onSaved={(result) => {
            const edited = pendingEdit;
            if (edited) {
              const moved = result.locations.find(
                (location) => location.save_folder === edited.saveFolder,
              );
              if (moved) {
                onSnapshotChange({
                  ...snapshot,
                  tracks: snapshot.tracks.map((item) =>
                    item.video_id === result.video_id
                      ? {
                          ...item,
                          local_path:
                            `${edited.saveFolder}/${moved.new_relative_path}`.replace(
                              /\/+/g,
                              "/",
                            ),
                        }
                      : item,
                  ),
                });
              }
            }
            setPendingEdit(null);
            onImported();
          }}
        />
      </div>
    </section>
  );
}
