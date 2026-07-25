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
  "relative z-20 flex h-full min-w-0 w-full items-center justify-end gap-0";

/**
 * Playlist / search card header action strip.
 * `pr-3` matches `TRACK_ROW_GRID`’s right padding so icon centers align with rows.
 */
export const SYNC_CARD_ACTIONS =
  "relative z-10 flex shrink-0 flex-row items-center justify-end gap-0 py-2 pr-3";

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
