import type { SyncTrackItem } from "@/api/sync-ledger";
import { listSyncTracks } from "@/api/sync-ledger";
import {
  fetchTrackLyrics,
  saveTrackLyrics,
  trackCoverUrl,
} from "@/api/library";
import { fetchSearchLyrics, type SearchLyrics } from "@/api/search";
import {
  cyclePlayMode,
  getPlayMode,
  setPlayMode,
  type PlayMode,
} from "@/features/sync/play-mode";
import {
  applyOffsetToLines,
  parseLyrics,
  shiftLrcTimestamps,
  type LyricLine,
} from "@/features/sync/lrc";
import { formatArtistTitle } from "@/features/sync/track-label";
import { basePath } from "@/lib/base-path";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

export type PlaylistTrack = {
  key: string;
  path: string;
  label?: string;
  /** Absolute audio URL for temporary previews (bypasses /library/file). */
  sourceUrl?: string;
};

export type LyricsSaveResult = {
  ok: boolean;
  sidecar: boolean;
  embedded: boolean;
  catalog: boolean;
  errors: string[];
  error?: string;
};

const SPECTRUM_BINS = 64;
const ADJUST_STEP = 0.2;
const ADJUST_STEP_FINE = 0.05;

const SEARCH_PREVIEW_PREFIX = "search-preview:";

/** Video ID for a search-preview playback key, or null for library tracks. */
function previewVideoId(key: string): string | null {
  return key.startsWith(SEARCH_PREVIEW_PREFIX)
    ? key.slice(SEARCH_PREVIEW_PREFIX.length)
    : null;
}

/**
 * Resolve lyrics for any playback key. Preview keys are not library files, so
 * they must query the on-demand search-lyrics endpoint by video ID instead of
 * the library path (which would 400).
 */
async function fetchLyricsForKey(key: string): Promise<SearchLyrics> {
  const vid = previewVideoId(key);
  if (vid) return fetchSearchLyrics(vid);
  return fetchTrackLyrics(key);
}

type AudioState = {
  key: string | null;
  activeFolder: string | null;
  playing: boolean;
  progress: number;
  currentTime: number;
  duration: number;
  playMode: PlayMode;
  nowPlayingLabel: string | null;
  lyricsAvailable: boolean;
  lyricsLines: LyricLine[];
  /** Lines with adjust-offset applied (for display / sync). */
  lyricsDisplayLines: LyricLine[];
  lyricsTimed: boolean;
  lyricsSource: string | null;
  lyricsContent: string | null;
  lyricsOffsetSec: number;
  lyricsAdjusting: boolean;
  lyricsEditing: boolean;
  subscribeSpectrum: (onStoreChange: () => void) => () => void;
  getSpectrumSnapshot: () => number[];
  play: (key: string, filePath: string, folder: string) => void;
  toggle: (key: string, filePath: string, folder: string) => void;
  playUrl: (
    key: string,
    url: string,
    folder: string,
    label: string,
    coverUrl?: string | null,
  ) => void;
  toggleUrl: (key: string, url: string, folder: string, label: string) => void;
  pause: () => void;
  seek: (ratio: number) => void;
  registerPlaylist: (folder: string, tracks: PlaylistTrack[]) => void;
  cyclePlayModeFor: (folder: string) => void;
  togglePlaylistFolder: (folder: string) => void;
  playPrevious: () => void;
  playNext: () => void;
  getPlayModeFor: (folder: string) => PlayMode;
  modesVersion: number;
  lyricsFullscreen: boolean;
  openLyricsFullscreen: () => void;
  closeLyricsFullscreen: () => void;
  lyricsHeaderVisible: boolean;
  toggleLyricsHeader: () => void;
  startLyricsAdjust: () => void;
  nudgeLyricsOffset: (deltaY: number, fine: boolean) => void;
  requestLeaveLyricsAdjust: () => void;
  confirmLeaveLyricsAdjust: (
    action: "save" | "discard",
  ) => Promise<LyricsSaveResult | null>;
  cancelLeaveLyricsAdjust: () => void;
  lyricsAdjustLeaveOpen: boolean;
  openLyricsEditor: () => void;
  /** Open the editor for an arbitrary library track (independent of playback). */
  openLyricsEditorFor: (
    key: string,
    initial: string | null,
    options?: { folder: string; previewOnly?: boolean },
  ) => void;
  closeLyricsEditor: () => void;
  /** Target key + initial content when editing a non-playing track. */
  lyricsEditorKey: string | null;
  lyricsEditorFolder: string | null;
  lyricsEditorInitial: string | null;
  /** When true, Save only commits draft text (no disk write). */
  lyricsEditorPreviewOnly: boolean;
  saveLyricsContent: (content: string) => Promise<LyricsSaveResult>;
  /** Commit draft lyrics back to the opener without writing files. */
  commitLyricsEditorDraft: (content: string) => void;
  reloadLyrics: () => Promise<void>;
};

/** Fired after lyrics are persisted anywhere so every view can resync. */
export type LyricsChangedDetail = { key: string; content: string };

const LibraryAudioContext = createContext<AudioState | null>(null);

function fileUrl(filePath: string): string {
  return `${basePath}/api/library/file?path=${encodeURIComponent(filePath)}`;
}

function trackKey(saveFolder: string, relativePath: string): string {
  return `${saveFolder}/${relativePath}`.replace(/\/+/g, "/");
}

/** Playing card headline uses Artist - Title only (not album-artist prefix). */
function formatTrackLabel(track: SyncTrackItem): string {
  return formatArtistTitle(track.artist, track.title);
}

function toPlaylistTracks(
  saveFolder: string,
  items: SyncTrackItem[],
): PlaylistTrack[] {
  return items
    .filter((t) => t.exists && t.storage !== "missing")
    .map((t) => ({
      key: trackKey(saveFolder, t.relative_path),
      path: trackKey(saveFolder, t.relative_path),
      label: formatTrackLabel(t),
    }));
}

function pickShuffleNext(
  tracks: PlaylistTrack[],
  currentKey: string | null,
): PlaylistTrack | null {
  if (tracks.length === 0) return null;
  if (tracks.length === 1) return tracks[0] ?? null;
  const others = currentKey
    ? tracks.filter((t) => t.key !== currentKey)
    : tracks;
  const pool = others.length > 0 ? others : tracks;
  return pool[Math.floor(Math.random() * pool.length)] ?? null;
}

/** Split "Artist - Title" / "AA · Artist - Title" for Media Session. */
function splitNowPlayingLabel(label: string | null): {
  title: string;
  artist: string;
} {
  if (!label?.trim()) return { title: "yubal", artist: "" };
  const dash = label.lastIndexOf(" - ");
  if (dash > 0) {
    return {
      artist: label.slice(0, dash).trim(),
      title: label.slice(dash + 3).trim() || label,
    };
  }
  return { title: label.trim(), artist: "" };
}

export function LibraryAudioProvider({ children }: { children: ReactNode }) {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const playlistsRef = useRef<Map<string, PlaylistTrack[]>>(new Map());
  const keyRef = useRef<string | null>(null);
  const activeFolderRef = useRef<string | null>(null);
  // Explicit artwork for non-library sources (e.g. search previews), whose key
  // is not a library path and so cannot resolve a cover via /library.
  const artworkRef = useRef<string | null>(null);
  const [artworkVersion, setArtworkVersion] = useState(0);
  const progressRafRef = useRef<number | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const mediaSourceConnectedRef = useRef(false);
  const spectrumBufferRef = useRef<Uint8Array | null>(null);
  const spectrumRef = useRef<number[]>([]);
  const spectrumListenersRef = useRef(new Set<() => void>());

  const [key, setKey] = useState<string | null>(null);
  const [activeFolder, setActiveFolder] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playMode, setPlayModeState] = useState<PlayMode>("single");
  const [modesVersion, setModesVersion] = useState(0);
  const [nowPlayingLabel, setNowPlayingLabel] = useState<string | null>(null);
  const [lyricsAvailable, setLyricsAvailable] = useState(false);
  const [lyricsLines, setLyricsLines] = useState<LyricLine[]>([]);
  const [lyricsTimed, setLyricsTimed] = useState(false);
  const [lyricsSource, setLyricsSource] = useState<string | null>(null);
  const [lyricsFullscreen, setLyricsFullscreen] = useState(false);
  const [lyricsHeaderVisible, setLyricsHeaderVisible] = useState(true);
  const [lyricsContent, setLyricsContent] = useState<string | null>(null);
  const [lyricsOffsetSec, setLyricsOffsetSec] = useState(0);
  const [lyricsAdjusting, setLyricsAdjusting] = useState(false);
  const [lyricsAdjustLeaveOpen, setLyricsAdjustLeaveOpen] = useState(false);
  const [lyricsEditing, setLyricsEditing] = useState(false);
  const [lyricsEditorKey, setLyricsEditorKey] = useState<string | null>(null);
  const [lyricsEditorFolder, setLyricsEditorFolder] = useState<string | null>(
    null,
  );
  const [lyricsEditorInitial, setLyricsEditorInitial] = useState<string | null>(
    null,
  );
  const [lyricsEditorPreviewOnly, setLyricsEditorPreviewOnly] = useState(false);
  const lyricsEditorKeyRef = useRef<string | null>(null);

  keyRef.current = key;
  activeFolderRef.current = activeFolder;
  lyricsEditorKeyRef.current = lyricsEditorKey;

  const ensurePlaylist = useCallback(async (folder: string) => {
    let tracks = playlistsRef.current.get(folder) ?? [];
    if (tracks.length === 0) {
      const items = await listSyncTracks(folder);
      tracks = toPlaylistTracks(folder, items);
      playlistsRef.current.set(folder, tracks);
    }
    return tracks;
  }, []);

  const resolveNextTrack = useCallback(
    async (
      folder: string,
      currentKey: string,
      mode: PlayMode,
    ): Promise<PlaylistTrack | null> => {
      const tracks = await ensurePlaylist(folder);
      const idx = tracks.findIndex((t) => t.key === currentKey);
      if (idx < 0) return null;

      switch (mode) {
        case "single":
          return null;
        case "single_loop":
          return tracks[idx] ?? null;
        case "loop":
          return tracks[(idx + 1) % tracks.length] ?? null;
        case "shuffle":
          return pickShuffleNext(tracks, currentKey);
        default:
          return null;
      }
    },
    [ensurePlaylist],
  );

  const pickStartTrack = useCallback(
    async (folder: string, mode: PlayMode): Promise<PlaylistTrack | null> => {
      const tracks = await ensurePlaylist(folder);
      if (tracks.length === 0) return null;
      if (mode === "shuffle") {
        return pickShuffleNext(tracks, null);
      }
      return tracks[0] ?? null;
    },
    [ensurePlaylist],
  );

  const labelFor = useCallback(
    (folder: string | null, trackKeyValue: string) => {
      if (!folder) return null;
      const tracks = playlistsRef.current.get(folder);
      return tracks?.find((t) => t.key === trackKeyValue)?.label ?? null;
    },
    [],
  );

  const playInternal = useCallback(
    (
      nextKey: string,
      path: string,
      folder: string,
      sourceIsUrl = false,
      explicitLabel?: string,
    ) => {
      const audio = audioRef.current;
      if (!audio) return;
      const source = sourceIsUrl ? path : fileUrl(path);
      if (keyRef.current !== nextKey) {
        audio.src = source;
        setKey(nextKey);
        setProgress(0);
        setNowPlayingLabel(explicitLabel ?? labelFor(folder, nextKey));
        spectrumRef.current = [];
        spectrumListenersRef.current.forEach((listener) => listener());
      } else if (!audio.src) {
        audio.src = source;
      }
      void audio.play().catch(() => {
        setPlaying(false);
      });
    },
    [labelFor],
  );

  const connectAnalyser = useCallback((audio: HTMLAudioElement) => {
    if (mediaSourceConnectedRef.current) return;
    const ctx = audioCtxRef.current ?? new AudioContext();
    audioCtxRef.current = ctx;
    const source = ctx.createMediaElementSource(audio);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.68;
    // Wider range so quieter highs still register
    analyser.minDecibels = -90;
    analyser.maxDecibels = -25;
    source.connect(analyser);
    analyser.connect(ctx.destination);
    analyserRef.current = analyser;
    spectrumBufferRef.current = new Uint8Array(analyser.frequencyBinCount);
    mediaSourceConnectedRef.current = true;
  }, []);

  const publishSpectrum = useCallback((levels: number[]) => {
    spectrumRef.current = levels;
    spectrumListenersRef.current.forEach((listener) => listener());
  }, []);

  const readSpectrum = useCallback(() => {
    const analyser = analyserRef.current;
    const buffer = spectrumBufferRef.current;
    if (!analyser || !buffer) return;
    analyser.getByteFrequencyData(buffer as Uint8Array<ArrayBuffer>);
    // Linear FFT bins put most musical energy on the left; ultra-high bins
    // rarely move. Use a compressed mapping over the useful musical range.
    const binCount = buffer.length;
    const usable = Math.max(8, Math.floor(binCount * 0.72));
    const levels: number[] = [];
    for (let i = 0; i < SPECTRUM_BINS; i++) {
      const t0 = i / SPECTRUM_BINS;
      const t1 = (i + 1) / SPECTRUM_BINS;
      // Mild log-ish curve: more resolution in lows/mids, still some highs
      const start = Math.max(1, Math.floor(Math.pow(t0, 1.45) * usable));
      const end = Math.max(start + 1, Math.floor(Math.pow(t1, 1.45) * usable));
      let peak = 0;
      for (let j = start; j < end && j < binCount; j++) {
        peak = Math.max(peak, buffer[j] ?? 0);
      }
      // Light gamma so quieter bands still show motion
      levels.push(Math.min(1, Math.pow(peak / 255, 0.7)));
    }
    publishSpectrum(levels);
  }, [publishSpectrum]);

  const clearSpectrum = useCallback(() => {
    publishSpectrum([]);
  }, [publishSpectrum]);

  const subscribeSpectrum = useCallback((onStoreChange: () => void) => {
    spectrumListenersRef.current.add(onStoreChange);
    return () => {
      spectrumListenersRef.current.delete(onStoreChange);
    };
  }, []);

  const getSpectrumSnapshot = useCallback(() => spectrumRef.current, []);

  useEffect(() => {
    const audio = new Audio();
    audio.preload = "metadata";
    audioRef.current = audio;

    const syncFrame = () => {
      const d = audio.duration;
      const finite = d > 0 && Number.isFinite(d);
      setCurrentTime(audio.currentTime);
      setDuration(finite ? d : 0);
      setProgress(finite ? audio.currentTime / d : 0);
      readSpectrum();
      progressRafRef.current = requestAnimationFrame(syncFrame);
    };

    const onPlay = () => {
      connectAnalyser(audio);
      void audioCtxRef.current?.resume();
      setPlaying(true);
      if (progressRafRef.current === null) {
        progressRafRef.current = requestAnimationFrame(syncFrame);
      }
    };

    const onPause = () => {
      setPlaying(false);
      clearSpectrum();
      if (progressRafRef.current !== null) {
        cancelAnimationFrame(progressRafRef.current);
        progressRafRef.current = null;
      }
    };

    const onEnded = () => {
      setPlaying(false);
      clearSpectrum();
      if (progressRafRef.current !== null) {
        cancelAnimationFrame(progressRafRef.current);
        progressRafRef.current = null;
      }
      setProgress(0);

      const currentKey = keyRef.current;
      const folder = activeFolderRef.current;
      if (!currentKey || !folder) return;

      const mode = getPlayMode(folder);
      void resolveNextTrack(folder, currentKey, mode).then((next) => {
        if (!next) return;
        if (next.sourceUrl) {
          playInternal(
            next.key,
            next.sourceUrl,
            folder,
            true,
            next.label ?? "",
          );
          return;
        }
        playInternal(next.key, next.path, folder);
      });
    };

    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onPause);
    audio.addEventListener("ended", onEnded);

    return () => {
      audio.pause();
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onPause);
      audio.removeEventListener("ended", onEnded);
      if (progressRafRef.current !== null) {
        cancelAnimationFrame(progressRafRef.current);
      }
      void audioCtxRef.current?.close();
      audioRef.current = null;
      analyserRef.current = null;
      mediaSourceConnectedRef.current = false;
    };
  }, [
    clearSpectrum,
    connectAnalyser,
    playInternal,
    readSpectrum,
    resolveNextTrack,
  ]);

  const play = useCallback(
    (nextKey: string, path: string, folder: string) => {
      activeFolderRef.current = folder;
      artworkRef.current = null;
      setArtworkVersion((v) => v + 1);
      setActiveFolder(folder);
      setPlayModeState(getPlayMode(folder));
      playInternal(nextKey, path, folder);
    },
    [playInternal],
  );

  const pause = useCallback(() => {
    audioRef.current?.pause();
  }, []);

  const toggle = useCallback(
    (nextKey: string, path: string, folder: string) => {
      const audio = audioRef.current;
      if (!audio) return;
      if (keyRef.current === nextKey && !audio.paused) {
        audio.pause();
        return;
      }
      play(nextKey, path, folder);
    },
    [play],
  );

  const playUrl = useCallback(
    (
      nextKey: string,
      url: string,
      folder: string,
      label: string,
      coverUrl?: string | null,
    ) => {
      activeFolderRef.current = folder;
      artworkRef.current = coverUrl ?? null;
      setArtworkVersion((v) => v + 1);
      setActiveFolder(folder);
      setPlayModeState(getPlayMode(folder));
      playInternal(nextKey, url, folder, true, label);
    },
    [playInternal],
  );

  const toggleUrl = useCallback(
    (nextKey: string, url: string, folder: string, label: string) => {
      const audio = audioRef.current;
      if (!audio) return;
      if (keyRef.current === nextKey && !audio.paused) {
        audio.pause();
        return;
      }
      playUrl(nextKey, url, folder, label);
    },
    [playUrl],
  );

  const playPlaylistTrack = useCallback(
    (folder: string, track: PlaylistTrack) => {
      if (track.sourceUrl) {
        playUrl(track.key, track.sourceUrl, folder, track.label ?? "");
        return;
      }
      play(track.key, track.path, folder);
    },
    [play, playUrl],
  );

  const seek = useCallback((ratio: number) => {
    const audio = audioRef.current;
    if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0)
      return;
    const clamped = Math.min(1, Math.max(0, ratio));
    audio.currentTime = clamped * audio.duration;
    setProgress(clamped);
  }, []);

  const registerPlaylist = useCallback(
    (folder: string, tracks: PlaylistTrack[]) => {
      playlistsRef.current.set(folder, tracks);
      if (activeFolderRef.current === folder && keyRef.current) {
        const label = tracks.find((t) => t.key === keyRef.current)?.label;
        if (label) setNowPlayingLabel(label);
      }
    },
    [],
  );

  const startFolderPlayback = useCallback(
    async (folder: string, mode?: PlayMode) => {
      const effectiveMode = mode ?? getPlayMode(folder);
      const track = await pickStartTrack(folder, effectiveMode);
      if (!track) return;
      playPlaylistTrack(folder, track);
    },
    [pickStartTrack, playPlaylistTrack],
  );

  const cyclePlayModeFor = useCallback((folder: string) => {
    const next = cyclePlayMode(getPlayMode(folder));
    setPlayMode(folder, next);
    setModesVersion((v) => v + 1);
    if (activeFolderRef.current === folder) {
      setPlayModeState(next);
    }
  }, []);

  const togglePlaylistFolder = useCallback(
    (folder: string) => {
      const audio = audioRef.current;
      const onThisFolder = activeFolderRef.current === folder;
      if (onThisFolder && audio && !audio.paused) {
        audio.pause();
        return;
      }
      if (onThisFolder && audio && audio.paused && keyRef.current) {
        void audio.play().catch(() => setPlaying(false));
        return;
      }
      void startFolderPlayback(folder);
    },
    [startFolderPlayback],
  );

  const playRelative = useCallback(
    async (delta: -1 | 1) => {
      const folder = activeFolderRef.current;
      const currentKey = keyRef.current;
      if (!folder || !currentKey) return;
      const tracks = await ensurePlaylist(folder);
      if (tracks.length === 0) return;
      const idx = tracks.findIndex((t) => t.key === currentKey);
      if (idx < 0) return;
      const nextIdx = (idx + delta + tracks.length) % tracks.length;
      const next = tracks[nextIdx];
      if (!next) return;
      playPlaylistTrack(folder, next);
    },
    [ensurePlaylist, playPlaylistTrack],
  );

  const playPrevious = useCallback(() => {
    void playRelative(-1);
  }, [playRelative]);

  const playNext = useCallback(() => {
    void playRelative(1);
  }, [playRelative]);

  // Browser / OS media controls (Chrome Global Media Controls, media keys).
  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;

    if (!key) {
      ms.metadata = null;
      ms.playbackState = "none";
      return;
    }

    const { title, artist } = splitNowPlayingLabel(nowPlayingLabel);
    // Preview keys are not library paths; use the explicit artwork (search
    // thumbnail) if provided, otherwise omit art rather than 404 the library.
    const coverBase = artworkRef.current
      ? artworkRef.current
      : previewVideoId(key)
        ? null
        : trackCoverUrl(key);
    const cover = coverBase
      ? new URL(coverBase, window.location.href).href
      : null;
    try {
      ms.metadata = new MediaMetadata({
        title,
        artist,
        album: activeFolder ?? "",
        artwork: cover
          ? [
              { src: cover, sizes: "96x96", type: "image/jpeg" },
              { src: cover, sizes: "256x256", type: "image/jpeg" },
              { src: cover, sizes: "512x512", type: "image/jpeg" },
            ]
          : [],
      });
    } catch {
      // Some browsers reject artwork URLs; metadata without art is fine.
      ms.metadata = new MediaMetadata({
        title,
        artist,
        album: activeFolder ?? "",
      });
    }
    ms.playbackState = playing ? "playing" : "paused";
  }, [key, nowPlayingLabel, activeFolder, playing, artworkVersion]);

  const positionSec = Math.floor(currentTime);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;
    if (
      !key ||
      !Number.isFinite(duration) ||
      duration <= 0 ||
      !Number.isFinite(currentTime)
    ) {
      return;
    }
    try {
      ms.setPositionState({
        duration,
        playbackRate: 1,
        position: Math.min(Math.max(0, currentTime), duration),
      });
    } catch {
      // Ignore InvalidStateError when nothing is playing.
    }
  }, [key, duration, playing, positionSec, currentTime]);

  useEffect(() => {
    if (!("mediaSession" in navigator)) return;
    const ms = navigator.mediaSession;

    const onPlay = () => {
      const audio = audioRef.current;
      if (!audio) return;
      void audio.play().catch(() => setPlaying(false));
    };
    const onPause = () => {
      audioRef.current?.pause();
    };
    const onStop = () => {
      const audio = audioRef.current;
      if (!audio) return;
      audio.pause();
      audio.currentTime = 0;
      setProgress(0);
      setCurrentTime(0);
    };
    const seekBy = (deltaSec: number) => {
      const audio = audioRef.current;
      if (!audio || !Number.isFinite(audio.duration) || audio.duration <= 0)
        return;
      const next = Math.min(
        audio.duration,
        Math.max(0, audio.currentTime + deltaSec),
      );
      audio.currentTime = next;
      setCurrentTime(next);
      setProgress(next / audio.duration);
    };
    const onSeekTo = (details: MediaSessionActionDetails) => {
      const audio = audioRef.current;
      if (
        !audio ||
        details.seekTime == null ||
        !Number.isFinite(audio.duration) ||
        audio.duration <= 0
      ) {
        return;
      }
      const next = Math.min(audio.duration, Math.max(0, details.seekTime));
      audio.currentTime = next;
      setCurrentTime(next);
      setProgress(next / audio.duration);
    };

    ms.setActionHandler("play", onPlay);
    ms.setActionHandler("pause", onPause);
    ms.setActionHandler("stop", onStop);
    ms.setActionHandler("previoustrack", () => {
      void playRelative(-1);
    });
    ms.setActionHandler("nexttrack", () => {
      void playRelative(1);
    });
    ms.setActionHandler("seekbackward", (d) => {
      seekBy(-(d.seekOffset ?? 10));
    });
    ms.setActionHandler("seekforward", (d) => {
      seekBy(d.seekOffset ?? 10);
    });
    ms.setActionHandler("seekto", onSeekTo);

    return () => {
      ms.setActionHandler("play", null);
      ms.setActionHandler("pause", null);
      ms.setActionHandler("stop", null);
      ms.setActionHandler("previoustrack", null);
      ms.setActionHandler("nexttrack", null);
      ms.setActionHandler("seekbackward", null);
      ms.setActionHandler("seekforward", null);
      ms.setActionHandler("seekto", null);
    };
  }, [playRelative]);

  const getPlayModeFor = useCallback(
    (folder: string) => getPlayMode(folder),
    // Re-read when modesVersion changes so icons update after cycling.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [modesVersion],
  );

  useEffect(() => {
    if (!key) {
      setLyricsAvailable(false);
      setLyricsLines([]);
      setLyricsTimed(false);
      setLyricsSource(null);
      setLyricsContent(null);
      setLyricsOffsetSec(0);
      setLyricsAdjusting(false);
      setLyricsAdjustLeaveOpen(false);
      setLyricsEditing(false);
      return;
    }
    let cancelled = false;
    void fetchLyricsForKey(key).then((res) => {
      if (cancelled) return;
      if (res.available && res.content) {
        const parsed = parseLyrics(res.content);
        setLyricsContent(res.content);
        setLyricsLines(parsed.lines);
        setLyricsTimed(parsed.timed);
        setLyricsAvailable(parsed.lines.length > 0);
        setLyricsSource(res.source ?? null);
      } else {
        setLyricsContent(null);
        setLyricsLines([]);
        setLyricsTimed(false);
        setLyricsAvailable(false);
        setLyricsSource(null);
      }
      setLyricsOffsetSec(0);
      setLyricsAdjusting(false);
      setLyricsAdjustLeaveOpen(false);
    });
    return () => {
      cancelled = true;
    };
  }, [key]);

  const reloadLyrics = useCallback(async () => {
    const k = keyRef.current;
    if (!k) return;
    const res = await fetchLyricsForKey(k);
    if (res.available && res.content) {
      const parsed = parseLyrics(res.content);
      setLyricsContent(res.content);
      setLyricsLines(parsed.lines);
      setLyricsTimed(parsed.timed);
      setLyricsAvailable(parsed.lines.length > 0);
      setLyricsSource(res.source ?? null);
    } else {
      setLyricsContent(null);
      setLyricsLines([]);
      setLyricsTimed(false);
      setLyricsAvailable(false);
      setLyricsSource(null);
    }
    setLyricsOffsetSec(0);
  }, []);

  const saveLyricsContent = useCallback(
    async (content: string): Promise<LyricsSaveResult> => {
      // Editing a non-playing track routes to its own key; otherwise the
      // currently playing/loaded track is used.
      const k = lyricsEditorKeyRef.current ?? keyRef.current;
      if (!k)
        return {
          ok: false,
          sidecar: false,
          embedded: false,
          catalog: false,
          errors: [],
          error: "no track",
        };
      const result = await saveTrackLyrics(k, content);
      if ("error" in result) {
        return {
          ok: false,
          sidecar: false,
          embedded: false,
          catalog: false,
          errors: [result.error],
          error: result.error,
        };
      }
      if (result.ok) {
        // Global sync: notify every view (header, fullscreen, edit-tags modal).
        window.dispatchEvent(
          new CustomEvent<LyricsChangedDetail>("yubal:lyrics-changed", {
            detail: { key: k, content },
          }),
        );
      }
      return result;
    },
    [],
  );

  // Keep the playing track's lyrics live whenever they change anywhere.
  useEffect(() => {
    const onChanged = (event: Event) => {
      const detail = (event as CustomEvent<LyricsChangedDetail>).detail;
      if (!detail || detail.key !== keyRef.current) return;
      const parsed = parseLyrics(detail.content);
      setLyricsContent(detail.content);
      setLyricsLines(parsed.lines);
      setLyricsTimed(parsed.timed);
      setLyricsAvailable(parsed.lines.length > 0);
      setLyricsOffsetSec(0);
    };
    window.addEventListener("yubal:lyrics-changed", onChanged);
    return () => window.removeEventListener("yubal:lyrics-changed", onChanged);
  }, []);

  const openLyricsFullscreen = useCallback(() => {
    setLyricsFullscreen(true);
  }, []);

  const closeLyricsFullscreen = useCallback(() => {
    setLyricsFullscreen(false);
    requestAnimationFrame(() => {
      const el = document.activeElement;
      if (el instanceof HTMLElement) el.blur();
    });
  }, []);

  const toggleLyricsHeader = useCallback(() => {
    setLyricsHeaderVisible((v) => !v);
  }, []);

  const startLyricsAdjust = useCallback(() => {
    if (!keyRef.current || !lyricsAvailable) return;
    setLyricsEditing(false);
    setLyricsAdjusting(true);
    setLyricsAdjustLeaveOpen(false);
    setLyricsOffsetSec(0);
  }, [lyricsAvailable]);

  const nudgeLyricsOffset = useCallback((deltaY: number, fine: boolean) => {
    const dir = deltaY > 0 ? 1 : deltaY < 0 ? -1 : 0;
    if (!dir) return;
    const step = fine ? ADJUST_STEP_FINE : ADJUST_STEP;
    // Wheel down → lyrics later (positive offset), same mental model as scrolling down.
    setLyricsOffsetSec((v) => Math.round((v + dir * step) * 100) / 100);
  }, []);

  const leaveAdjustClean = useCallback(() => {
    setLyricsAdjusting(false);
    setLyricsAdjustLeaveOpen(false);
    setLyricsOffsetSec(0);
  }, []);

  const confirmLeaveLyricsAdjust = useCallback(
    async (action: "save" | "discard"): Promise<LyricsSaveResult | null> => {
      if (action === "discard") {
        leaveAdjustClean();
        return null;
      }
      const base = lyricsContent;
      const offset = lyricsOffsetSec;
      if (!base) {
        leaveAdjustClean();
        return null;
      }
      const next = shiftLrcTimestamps(base, offset);
      const result = await saveLyricsContent(next);
      if (result.ok) leaveAdjustClean();
      return result;
    },
    [leaveAdjustClean, lyricsContent, lyricsOffsetSec, saveLyricsContent],
  );

  const cancelLeaveLyricsAdjust = useCallback(() => {
    setLyricsAdjustLeaveOpen(false);
  }, []);

  // Click-outside / Esc leave: if offset≈0, exit without dialog.
  const requestLeaveLyricsAdjustSafe = useCallback(() => {
    if (!lyricsAdjusting) return;
    if (Math.abs(lyricsOffsetSec) < 0.001) {
      leaveAdjustClean();
      return;
    }
    setLyricsAdjustLeaveOpen(true);
  }, [leaveAdjustClean, lyricsAdjusting, lyricsOffsetSec]);

  const openLyricsEditor = useCallback(() => {
    if (!keyRef.current) return;
    setLyricsAdjusting(false);
    setLyricsAdjustLeaveOpen(false);
    setLyricsOffsetSec(0);
    setLyricsEditorKey(null);
    setLyricsEditorFolder(null);
    setLyricsEditorInitial(null);
    setLyricsEditorPreviewOnly(false);
    setLyricsEditing(true);
  }, []);

  const openLyricsEditorFor = useCallback(
    (
      targetKey: string,
      initial: string | null,
      options?: { folder: string; previewOnly?: boolean },
    ) => {
      if (!targetKey) return;
      setLyricsAdjusting(false);
      setLyricsAdjustLeaveOpen(false);
      setLyricsOffsetSec(0);
      setLyricsEditorKey(targetKey);
      setLyricsEditorFolder(options?.folder ?? null);
      setLyricsEditorInitial(initial ?? "");
      setLyricsEditorPreviewOnly(Boolean(options?.previewOnly));
      setLyricsEditing(true);
    },
    [],
  );

  const closeLyricsEditor = useCallback(() => {
    setLyricsEditing(false);
    setLyricsEditorKey(null);
    setLyricsEditorFolder(null);
    setLyricsEditorInitial(null);
    setLyricsEditorPreviewOnly(false);
  }, []);

  const commitLyricsEditorDraft = useCallback((content: string) => {
    const k = lyricsEditorKeyRef.current;
    if (!k) return;
    // Preview-only draft for the opener (edit-tags). Does NOT update the
    // playing display — that only refreshes after a real disk write.
    window.dispatchEvent(
      new CustomEvent<LyricsChangedDetail>("yubal:lyrics-draft", {
        detail: { key: k, content },
      }),
    );
  }, []);

  const lyricsDisplayLines = useMemo(
    () =>
      lyricsTimed
        ? applyOffsetToLines(lyricsLines, lyricsOffsetSec)
        : lyricsLines,
    [lyricsLines, lyricsOffsetSec, lyricsTimed],
  );

  const value = useMemo(
    () => ({
      key,
      activeFolder,
      playing,
      progress,
      currentTime,
      duration,
      playMode,
      nowPlayingLabel,
      lyricsAvailable,
      lyricsLines,
      lyricsDisplayLines,
      lyricsTimed,
      lyricsSource,
      lyricsContent,
      lyricsOffsetSec,
      lyricsAdjusting,
      lyricsEditing,
      subscribeSpectrum,
      getSpectrumSnapshot,
      play,
      toggle,
      playUrl,
      toggleUrl,
      pause,
      seek,
      registerPlaylist,
      cyclePlayModeFor,
      togglePlaylistFolder,
      playPrevious,
      playNext,
      getPlayModeFor,
      modesVersion,
      lyricsFullscreen,
      openLyricsFullscreen,
      closeLyricsFullscreen,
      lyricsHeaderVisible,
      toggleLyricsHeader,
      startLyricsAdjust,
      nudgeLyricsOffset,
      requestLeaveLyricsAdjust: requestLeaveLyricsAdjustSafe,
      confirmLeaveLyricsAdjust,
      cancelLeaveLyricsAdjust,
      lyricsAdjustLeaveOpen,
      openLyricsEditor,
      openLyricsEditorFor,
      closeLyricsEditor,
      lyricsEditorKey,
      lyricsEditorFolder,
      lyricsEditorInitial,
      lyricsEditorPreviewOnly,
      saveLyricsContent,
      commitLyricsEditorDraft,
      reloadLyrics,
    }),
    [
      key,
      activeFolder,
      playing,
      progress,
      currentTime,
      duration,
      playMode,
      nowPlayingLabel,
      lyricsAvailable,
      lyricsLines,
      lyricsDisplayLines,
      lyricsTimed,
      lyricsSource,
      lyricsContent,
      lyricsOffsetSec,
      lyricsAdjusting,
      lyricsEditing,
      subscribeSpectrum,
      getSpectrumSnapshot,
      play,
      toggle,
      playUrl,
      toggleUrl,
      pause,
      seek,
      registerPlaylist,
      cyclePlayModeFor,
      togglePlaylistFolder,
      playPrevious,
      playNext,
      getPlayModeFor,
      modesVersion,
      lyricsFullscreen,
      openLyricsFullscreen,
      closeLyricsFullscreen,
      lyricsHeaderVisible,
      toggleLyricsHeader,
      startLyricsAdjust,
      nudgeLyricsOffset,
      requestLeaveLyricsAdjustSafe,
      confirmLeaveLyricsAdjust,
      cancelLeaveLyricsAdjust,
      lyricsAdjustLeaveOpen,
      openLyricsEditor,
      openLyricsEditorFor,
      closeLyricsEditor,
      lyricsEditorKey,
      lyricsEditorFolder,
      lyricsEditorInitial,
      lyricsEditorPreviewOnly,
      saveLyricsContent,
      commitLyricsEditorDraft,
      reloadLyrics,
    ],
  );

  return (
    <LibraryAudioContext.Provider value={value}>
      {children}
    </LibraryAudioContext.Provider>
  );
}

export function useLibraryAudio(): AudioState {
  const ctx = useContext(LibraryAudioContext);
  if (!ctx) {
    throw new Error("useLibraryAudio requires LibraryAudioProvider");
  }
  return ctx;
}

export function useSpectrumLevels(): number[] {
  const { subscribeSpectrum, getSpectrumSnapshot } = useLibraryAudio();
  return useSyncExternalStore(subscribeSpectrum, getSpectrumSnapshot);
}
