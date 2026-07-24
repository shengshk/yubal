import {
  activeLyricIndex,
  lyricOpacity,
  lyricScale,
  type LyricLine,
} from "@/features/sync/lrc";
import { LyricsOffsetBadge } from "@/features/sync/lyrics-adjust";
import { useLibraryAudio } from "@/features/sync/library-audio";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

type CompactProps = {
  onOpenFullscreen: () => void;
};

/** Native wheel seek — must run on a non-passive listener so preventDefault works. */
function applyWheelSeek(
  e: WheelEvent,
  progress: number,
  seek: (ratio: number) => void,
  step = 0.018,
) {
  e.preventDefault();
  e.stopPropagation();
  const dir = e.deltaY > 0 ? 1 : e.deltaY < 0 ? -1 : 0;
  if (!dir) return;
  seek(Math.min(1, Math.max(0, progress + dir * step)));
}

/** Compact 3-line lyrics in the header (theme-aware, right-aligned). */
export function CompactLyricsDisplay({ onOpenFullscreen }: CompactProps) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const lines = audio.lyricsDisplayLines;
  const idx = activeLyricIndex(
    lines,
    audio.currentTime,
    audio.progress,
    audio.lyricsTimed,
  );
  const playing = Boolean(audio.key);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const progressRef = useRef(audio.progress);
  const seekRef = useRef(audio.seek);
  const canWheelRef = useRef(false);
  const adjustingRef = useRef(audio.lyricsAdjusting);
  const nudgeRef = useRef(audio.nudgeLyricsOffset);

  progressRef.current = audio.progress;
  seekRef.current = audio.seek;
  canWheelRef.current = Boolean(playing && audio.lyricsAvailable);
  adjustingRef.current = audio.lyricsAdjusting;
  nudgeRef.current = audio.nudgeLyricsOffset;

  const prev = idx > 0 ? lines[idx - 1] : null;
  const cur = idx >= 0 ? lines[idx] : null;
  const next = idx >= 0 && idx + 1 < lines.length ? lines[idx + 1] : null;

  const emptyHint =
    playing && !audio.lyricsAvailable ? t("sync.lyricsUnavailableShort") : "";

  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [menu]);

  useEffect(() => {
    const el = btnRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      if (!canWheelRef.current) return;
      e.preventDefault();
      e.stopPropagation();
      if (adjustingRef.current) {
        nudgeRef.current(e.deltaY, e.shiftKey);
        return;
      }
      applyWheelSeek(e, progressRef.current, seekRef.current, 0.012);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  return (
    <>
      <div
        data-lyrics-adjust-zone=""
        className="flex max-w-[min(420px,46vw)] min-w-0 items-end gap-1.5"
      >
        <button
          ref={btnRef}
          type="button"
          className={`lyrics-type hover:bg-default-100/40 flex min-w-0 flex-1 flex-col items-end gap-0 rounded-md px-1.5 py-0.5 text-right ring-0 transition-colors outline-none focus:outline-none focus-visible:ring-0 focus-visible:outline-none disabled:cursor-default disabled:opacity-70 disabled:hover:bg-transparent ${
            audio.lyricsAdjusting ? "bg-primary/10" : ""
          }`}
          onClick={() => {
            if (!audio.lyricsAvailable) return;
            if (audio.lyricsAdjusting) return;
            onOpenFullscreen();
          }}
          onContextMenu={(e) => {
            e.preventDefault();
            if (!audio.lyricsAvailable) return;
            setMenu({ x: e.clientX, y: e.clientY });
          }}
          disabled={!playing || !audio.lyricsAvailable}
          aria-label={t("sync.lyricsPanel")}
        >
          {audio.lyricsAvailable && cur ? (
            <>
              <span
                className="text-foreground w-full truncate text-[0.7rem] leading-none tracking-wide transition-[opacity,transform] duration-300"
                style={{
                  opacity: lyricOpacity(1, 1.45),
                  transform: `scale(${lyricScale(1, 2.5)})`,
                  transformOrigin: "right center",
                }}
              >
                {prev?.text || "\u00a0"}
              </span>
              <span
                className="text-default-700 dark:text-default-300 w-full truncate text-[0.95rem] leading-tight font-medium tracking-wide transition-[opacity,transform] duration-300"
                style={{ opacity: 0.92 }}
              >
                {cur.text}
              </span>
              <span
                className="text-foreground w-full truncate text-[0.7rem] leading-none tracking-wide transition-[opacity,transform] duration-300"
                style={{
                  opacity: lyricOpacity(1, 1.45),
                  transform: `scale(${lyricScale(1, 2.5)})`,
                  transformOrigin: "right center",
                }}
              >
                {next?.text || "\u00a0"}
              </span>
            </>
          ) : (
            <span className="text-foreground-400 text-xs font-light tracking-wide">
              {emptyHint || "\u00a0"}
            </span>
          )}
        </button>
        <LyricsOffsetBadge className="mb-1 shrink-0" />
      </div>
      {menu ? (
        <div
          data-lyrics-adjust-zone=""
          className="bg-content1 fixed z-[110] w-max overflow-hidden rounded-md shadow-lg"
          style={{ left: menu.x, top: menu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="hover:bg-default-100 text-foreground block w-full px-2.5 py-1 text-left text-sm leading-snug"
            onClick={() => {
              setMenu(null);
              audio.startLyricsAdjust();
            }}
          >
            {t("sync.lyricsAdjust")}
          </button>
          <button
            type="button"
            className="hover:bg-default-100 text-foreground block w-full px-2.5 py-1 text-left text-sm leading-snug"
            onClick={() => {
              setMenu(null);
              audio.openLyricsEditor();
            }}
          >
            {t("sync.lyricsEdit")}
          </button>
        </div>
      ) : null}
    </>
  );
}

type FullscreenProps = {
  open: boolean;
  onClose: () => void;
};

/** Soft teal accent (UI primary family) — not neon success green. */
const ACTIVE = "rgba(140, 190, 182, 0.98)";
const IDLE = "255, 255, 255";

/**
 * Fullscreen font size: clamp with vmin (min of vw/vh).
 * Industry default for karaoke UIs — tracks the smaller viewport side,
 * not area (area over-reacts on ultrawide) and not width-only (too big on tall windows).
 */
const FS_FONT = "clamp(1.2rem, 2.6vmin + 0.65rem, 2.85rem)";

/** Drag: full viewport height ≈ ~28% seek (was ~50%+). */
const DRAG_SEEK_SPAN = 3.6;

/** Fullscreen lyrics: always dark; drag / wheel seek; context-menu stubs. */
export function FullscreenLyrics({ open, onClose }: FullscreenProps) {
  const { t } = useTranslation();
  const audio = useLibraryAudio();
  const lines = audio.lyricsDisplayLines;
  const viewportRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{
    pointerId: number;
    startY: number;
    startProgress: number;
    moved: boolean;
    wasPlaying: boolean;
    pending: number;
  } | null>(null);
  const scrubRef = useRef<number | null>(null);
  const progressRef = useRef(audio.progress);
  const seekRef = useRef(audio.seek);
  const adjustingRef = useRef(audio.lyricsAdjusting);
  const nudgeRef = useRef(audio.nudgeLyricsOffset);
  const menuRef = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [shiftY, setShiftY] = useState(0);
  const [ready, setReady] = useState(false);
  /** Visual scrub only — commit audio.seek on pointerup to avoid scrub noise. */
  const [scrubProgress, setScrubProgress] = useState<number | null>(null);

  scrubRef.current = scrubProgress;
  progressRef.current = audio.progress;
  seekRef.current = audio.seek;
  adjustingRef.current = audio.lyricsAdjusting;
  nudgeRef.current = audio.nudgeLyricsOffset;

  const displayProgress = scrubProgress ?? audio.progress;
  const displayTime =
    scrubProgress != null &&
    Number.isFinite(audio.duration) &&
    audio.duration > 0
      ? scrubProgress * audio.duration
      : audio.currentTime;

  const idx = activeLyricIndex(
    lines,
    displayTime,
    displayProgress,
    audio.lyricsTimed,
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (audio.lyricsEditing || audio.lyricsAdjustLeaveOpen) return;
      if (audio.lyricsAdjusting) {
        e.preventDefault();
        audio.requestLeaveLyricsAdjust();
        return;
      }
      onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, audio]);

  useEffect(() => {
    if (!menu) return;
    const closeOutside = (e: PointerEvent) => {
      const target = e.target;
      if (target instanceof Node && menuRef.current?.contains(target)) return;
      setMenu(null);
    };
    window.addEventListener("pointerdown", closeOutside, true);
    return () => window.removeEventListener("pointerdown", closeOutside, true);
  }, [menu]);

  useEffect(() => {
    if (!open) setScrubProgress(null);
  }, [open]);

  // Non-passive wheel — React's onWheel is passive and cannot preventDefault.
  useEffect(() => {
    if (!open) return;
    const el = viewportRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      e.stopPropagation();
      if (adjustingRef.current) {
        nudgeRef.current(e.deltaY, e.shiftKey);
        return;
      }
      applyWheelSeek(
        e,
        scrubRef.current ?? progressRef.current,
        seekRef.current,
        0.022,
      );
    };
    const onSelectStart = (e: Event) => e.preventDefault();
    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("selectstart", onSelectStart);
    return () => {
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("selectstart", onSelectStart);
    };
  }, [open]);

  // Pin active line to exact viewport vertical center via translateY.
  useLayoutEffect(() => {
    if (!open) {
      setReady(false);
      setShiftY(0);
      return;
    }
    const viewport = viewportRef.current;
    const list = listRef.current;
    if (!viewport || !list) return;

    const place = () => {
      const active =
        idx >= 0
          ? list.querySelector<HTMLElement>(`[data-lyric-index="${idx}"]`)
          : null;
      if (!active) {
        setShiftY(viewport.clientHeight / 2);
        setReady(true);
        return;
      }
      const activeCenter = active.offsetTop + active.offsetHeight / 2;
      setShiftY(viewport.clientHeight / 2 - activeCenter);
      setReady(true);
    };

    place();
    const ro = new ResizeObserver(place);
    ro.observe(viewport);
    window.addEventListener("resize", place);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", place);
    };
  }, [open, idx, lines]);

  const togglePlayPause = useCallback(() => {
    const folder = audio.activeFolder;
    if (!folder) return;
    audio.togglePlaylistFolder(folder);
  }, [audio]);

  const requestClose = () => {
    if (audio.lyricsAdjusting) {
      audio.requestLeaveLyricsAdjust();
      return;
    }
    onClose();
  };

  if (!open) return null;

  return (
    <div
      data-lyrics-adjust-zone=""
      className="fixed inset-0 z-[100] flex flex-col bg-[#0a0a0a] text-white select-none"
      role="dialog"
      aria-modal
      aria-label={t("sync.lyricsFullscreen")}
      onClick={requestClose}
      onContextMenu={(e) => {
        e.preventDefault();
        setMenu({ x: e.clientX, y: e.clientY });
      }}
    >
      <LyricsOffsetBadge className="pointer-events-none absolute top-4 right-5 z-10 text-white/45" />
      {(
        [
          "left-0 top-0",
          "right-0 top-0",
          "left-0 bottom-0",
          "right-0 bottom-0",
        ] as const
      ).map((pos) => (
        <button
          key={pos}
          type="button"
          aria-label={t("sync.lyricsFullscreenExit")}
          className={`absolute z-20 h-[min(9rem,18vmin)] w-[min(9rem,18vmin)] cursor-pointer border-0 bg-transparent ${pos}`}
          onClick={(e) => {
            e.stopPropagation();
            requestClose();
          }}
        />
      ))}
      <div
        ref={viewportRef}
        className="lyrics-fs-mask relative min-h-0 flex-1 cursor-default overflow-hidden select-none"
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          togglePlayPause();
        }}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          // Don't start scrub from corner exit zones (they're above us).
          (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
          const start = scrubProgress ?? audio.progress;
          dragRef.current = {
            pointerId: e.pointerId,
            startY: e.clientY,
            startProgress: start,
            moved: false,
            wasPlaying: audio.playing,
            pending: start,
          };
        }}
        onPointerMove={(e) => {
          const drag = dragRef.current;
          if (!drag || drag.pointerId !== e.pointerId) return;
          const dy = drag.startY - e.clientY;
          if (!drag.moved && Math.abs(dy) > 6) {
            drag.moved = true;
            // Pause only after a real scrub starts — avoids decoder glitches.
            if (drag.wasPlaying) audio.pause();
          }
          if (!drag.moved) return;
          const delta = dy / Math.max(720, window.innerHeight * DRAG_SEEK_SPAN);
          const next = Math.min(1, Math.max(0, drag.startProgress + delta));
          drag.pending = next;
          // Visual-only during drag — commit currentTime on pointerup.
          setScrubProgress(next);
        }}
        onPointerUp={(e) => {
          const drag = dragRef.current;
          if (!drag || drag.pointerId !== e.pointerId) return;
          dragRef.current = null;
          if (!drag.moved) return;
          audio.seek(drag.pending);
          setScrubProgress(null);
          if (drag.wasPlaying && audio.activeFolder) {
            audio.togglePlaylistFolder(audio.activeFolder);
          }
        }}
        onPointerCancel={() => {
          const drag = dragRef.current;
          dragRef.current = null;
          if (!drag?.moved) {
            setScrubProgress(null);
            return;
          }
          audio.seek(drag.pending);
          setScrubProgress(null);
          if (drag.wasPlaying && audio.activeFolder) {
            audio.togglePlaylistFolder(audio.activeFolder);
          }
        }}
      >
        <div
          ref={listRef}
          className="lyrics-type-fs absolute inset-x-0 top-0 flex flex-col items-center gap-[0.28em] px-8 will-change-transform select-none"
          style={{
            fontSize: FS_FONT,
            transform: `translate3d(0, ${shiftY}px, 0)`,
            transition: ready
              ? "transform 0.4s cubic-bezier(0.22, 1, 0.36, 1)"
              : "none",
            opacity: ready ? 1 : 0,
          }}
        >
          {lines.length === 0 ? (
            <p className="text-[0.55em] font-normal tracking-wide text-white/35">
              {t("sync.lyricsUnavailableShort")}
            </p>
          ) : (
            lines.map((line, absoluteIndex) => {
              const distance = Math.abs(absoluteIndex - idx);
              const active = absoluteIndex === idx;
              const op = active ? 1 : lyricOpacity(distance, 9);
              return (
                <button
                  key={`${absoluteIndex}-${line.time}`}
                  type="button"
                  data-lyric-index={absoluteIndex}
                  className={`max-w-[calc(100vw-4rem)] text-center leading-[1.35] tracking-[0.03em] transition-[opacity,color,font-size] duration-300 ease-out select-none ${
                    active ? "lyrics-fs-emboss-active" : "lyrics-fs-emboss"
                  }`}
                  style={{
                    fontSize: active ? "120%" : "100%",
                    fontWeight: active ? 500 : 400,
                    color: active ? ACTIVE : `rgba(${IDLE}, ${op * 0.72})`,
                  }}
                  onClick={() => {
                    if (dragRef.current?.moved) return;
                    if (
                      !Number.isFinite(audio.duration) ||
                      audio.duration <= 0
                    ) {
                      return;
                    }
                    if (audio.lyricsTimed) {
                      audio.seek(
                        Math.min(1, Math.max(0, line.time / audio.duration)),
                      );
                    }
                  }}
                >
                  {line.text}
                </button>
              );
            })
          )}
        </div>
      </div>

      {menu ? (
        <div
          ref={menuRef}
          data-lyrics-adjust-zone=""
          className="fixed z-[110] w-max overflow-hidden rounded-md bg-[#161616] shadow-lg select-none"
          style={{ left: menu.x, top: menu.y }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="block w-full px-2.5 py-1 text-left text-sm leading-snug text-white/85 hover:bg-white/8"
            onClick={() => {
              setMenu(null);
              audio.startLyricsAdjust();
            }}
          >
            {t("sync.lyricsAdjust")}
          </button>
          <button
            type="button"
            className="block w-full px-2.5 py-1 text-left text-sm leading-snug text-white/85 hover:bg-white/8"
            onClick={() => {
              setMenu(null);
              audio.openLyricsEditor();
            }}
          >
            {t("sync.lyricsEdit")}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** Re-export type for callers. */
export type { LyricLine };
