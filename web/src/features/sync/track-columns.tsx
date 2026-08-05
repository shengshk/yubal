import { Button } from "@heroui/react";
import type { ReactNode } from "react";

/** Shared track-row grid: fixed index + actions so text columns align across rows.
 *  Narrow: Artist | Album
 *  Wide (md+): Title | Artist | Album | Album Artist
 *
 *  Avoid `display: contents` — it breaks CSS grid participation in common browsers
 *  and causes column drift between rows.
 *
 *  Horizontal padding (`px-3`) MUST stay in sync with `SYNC_CARD_ACTIONS` so the
 *  rightmost card icon and row action icons share the same center line.
 */
export const TRACK_ROW_GRID =
  "grid h-full w-full grid-cols-[2.25rem_minmax(0,1fr)_minmax(0,1fr)_minmax(0,10.5rem)] items-center gap-x-3 overflow-hidden px-3 md:grid-cols-[2.25rem_minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,10.5rem)]";

export const TRACK_INDEX =
  "text-foreground-400 flex w-full min-w-0 items-center justify-end gap-0.5 tabular-nums";

/** Fixed slot so the playing icon never shifts the index number. */
export const TRACK_INDEX_ICON =
  "flex h-3 w-3 shrink-0 items-center justify-center";

export const TRACK_CELL = "min-w-0 overflow-hidden truncate text-xs leading-none";

export const TRACK_CELL_WIDE = `${TRACK_CELL} hidden md:block`;

export const TRACK_ACTIONS =
  "relative z-20 grid h-full min-w-0 w-full grid-cols-6 items-center justify-items-center gap-0 overflow-hidden";

/** One of the six fixed semantic action columns. */
export const TRACK_ACTION_SLOT =
  "flex h-7 w-7 items-center justify-center";

export function TrackActionSlot({
  children,
  fallbackIcon,
  fallbackLabel,
}: {
  children?: ReactNode;
  fallbackIcon: ReactNode;
  fallbackLabel: string;
}) {
  return (
    <span className={TRACK_ACTION_SLOT}>
      {children ?? (
        <Button
          variant="light"
          size="sm"
          isIconOnly
          isDisabled
          className={`${SYNC_ACTION_BTN} opacity-40`}
          aria-label={fallbackLabel}
          title={fallbackLabel}
        >
          {fallbackIcon}
        </Button>
      )}
    </span>
  );
}

/**
 * Playlist / search card header action strip.
 * `pr-3` matches `TRACK_ROW_GRID`’s right padding so icon centers align with rows.
 */
export const SYNC_CARD_ACTIONS =
  "relative z-10 flex h-full max-h-20 shrink-0 flex-row items-center justify-end gap-0 overflow-hidden py-2 pr-3";

/** Fixed-height playlist header. Content clips instead of growing the card. */
export const SYNC_CARD_HEADER =
  "relative flex h-20 min-h-20 max-h-20 flex-row items-center gap-3 overflow-hidden p-0";

/** Compact content column inside the fixed playlist header. */
export const SYNC_CARD_CONTENT =
  "relative z-10 flex h-full min-h-0 min-w-0 flex-1 items-center overflow-hidden py-2 pr-0 text-left outline-none";

/** Shared icon-button size for card + row actions. */
export const SYNC_ACTION_BTN = "text-foreground-500 h-7 w-7 min-w-7";

export function TrackTextCells({
  title,
  artist,
  album,
  albumArtist,
  passThroughClicks = false,
}: {
  title: string;
  artist?: string | null;
  album?: string | null;
  albumArtist?: string | null;
  /** When true, clicks fall through to an underlay play target. */
  passThroughClicks?: boolean;
}) {
  const pass = passThroughClicks ? " pointer-events-none relative z-10" : "";
  // Singles frequently carry no album tags; fall back so every row keeps four
  // filled columns instead of collapsing to three.
  const albumText = album || title;
  const albumArtistText = albumArtist || artist || "";
  return (
    <>
      <span className={`${TRACK_CELL_WIDE}${pass}`}>{title}</span>
      <span className={`${TRACK_CELL}${pass}`}>{artist || ""}</span>
      <span className={`${TRACK_CELL}${pass}`}>{albumText}</span>
      <span className={`${TRACK_CELL_WIDE}${pass}`}>{albumArtistText}</span>
    </>
  );
}
