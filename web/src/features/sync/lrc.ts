/** Parse LRC / plain lyrics into timed lines for the player. */

export type LyricLine = {
  time: number;
  text: string;
};

export type ParsedLyrics = {
  lines: LyricLine[];
  timed: boolean;
};

const TAG_RE = /\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]/g;
/** Only mm:ss(.xx) tags — not [ti:] / [ar:] metadata. */
const TIME_TAG_RE = /\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]/g;

function parseTimestamp(
  min: string,
  sec: string,
  frac: string | undefined,
): number {
  const minutes = Number.parseInt(min, 10);
  const seconds = Number.parseInt(sec, 10);
  let millis = 0;
  if (frac) {
    const padded =
      frac.length === 1 ? frac + "00" : frac.length === 2 ? frac + "0" : frac;
    millis = Number.parseInt(padded.slice(0, 3), 10);
  }
  return minutes * 60 + seconds + millis / 1000;
}

/** Format seconds as [mm:ss.xx] (centiseconds). */
export function formatLrcTime(seconds: number): string {
  const t = Math.max(0, seconds);
  const m = Math.floor(t / 60);
  const s = t - m * 60;
  const whole = Math.floor(s);
  const cs = Math.round((s - whole) * 100);
  const csClamped = cs === 100 ? 99 : cs;
  return `[${String(m).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${String(csClamped).padStart(2, "0")}]`;
}

export function parseLyrics(content: string): ParsedLyrics {
  const timed: LyricLine[] = [];
  const plain: string[] = [];

  for (const raw of content.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;

    TAG_RE.lastIndex = 0;
    const stamps: number[] = [];
    let match: RegExpExecArray | null;
    let lastIndex = 0;
    while ((match = TAG_RE.exec(line)) !== null) {
      stamps.push(parseTimestamp(match[1]!, match[2]!, match[3]));
      lastIndex = TAG_RE.lastIndex;
    }

    if (stamps.length > 0) {
      const text = line.slice(lastIndex).trim();
      if (!text) continue;
      for (const time of stamps) {
        timed.push({ time, text });
      }
    } else if (!line.startsWith("[")) {
      plain.push(line);
    }
  }

  if (timed.length > 0) {
    timed.sort((a, b) => a.time - b.time || a.text.localeCompare(b.text));
    return { lines: timed, timed: true };
  }

  return {
    lines: plain.map((text, i) => ({ time: i, text })),
    timed: false,
  };
}

/** Shift every mm:ss timestamp in LRC text by offsetSec (metadata tags untouched). */
export function shiftLrcTimestamps(content: string, offsetSec: number): string {
  if (!offsetSec || !content) return content;
  return content.replace(TIME_TAG_RE, (_full, min, sec, frac) => {
    const next = Math.max(0, parseTimestamp(min, sec, frac) + offsetSec);
    return formatLrcTime(next);
  });
}

export function applyOffsetToLines(
  lines: LyricLine[],
  offsetSec: number,
): LyricLine[] {
  if (!offsetSec) return lines;
  return lines.map((l) => ({
    ...l,
    time: Math.max(0, l.time + offsetSec),
  }));
}

export function activeLyricIndex(
  lines: LyricLine[],
  currentTime: number,
  progress: number,
  timed: boolean,
): number {
  if (lines.length === 0) return -1;
  if (!timed) {
    return Math.min(
      lines.length - 1,
      Math.max(0, Math.floor(progress * lines.length)),
    );
  }
  let idx = 0;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i]!.time <= currentTime + 0.05) idx = i;
    else break;
  }
  return idx;
}

/** Linear fade by distance from the active line (0 = current). */
export function lyricOpacity(distance: number, maxDistance = 5): number {
  if (distance <= 0) return 1;
  const t = Math.min(1, distance / maxDistance);
  return Math.max(0.1, 1 - t * 0.9);
}

/** Scale by distance — current larger, farther lines smaller. */
export function lyricScale(distance: number, maxDistance = 5): number {
  if (distance <= 0) return 1;
  const t = Math.min(1, distance / maxDistance);
  return Math.max(0.72, 1 - t * 0.28);
}

export type TextLineInfo = {
  index: number;
  start: number;
  end: number;
  text: string;
};

/** Line containing cursor (0-based index). */
export function lineAtCursor(content: string, cursor: number): TextLineInfo {
  const lines = content.split("\n");
  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    const text = lines[i] ?? "";
    const end = pos + text.length;
    if (cursor <= end || i === lines.length - 1) {
      return { index: i, start: pos, end, text };
    }
    pos = end + 1;
  }
  return { index: 0, start: 0, end: 0, text: lines[0] ?? "" };
}

export function replaceLineTimestamps(line: string, timeSec: number): string {
  const tag = formatLrcTime(timeSec);
  const stripped = line.replace(TIME_TAG_RE, "").trimStart();
  if (!stripped) return tag;
  return `${tag}${stripped}`;
}

export function stripLineTimestamps(line: string): string {
  return line.replace(TIME_TAG_RE, "").trimStart();
}

export function stripAllTimestamps(content: string): string {
  return content
    .split("\n")
    .map((line) => stripLineTimestamps(line))
    .join("\n");
}

/** First time tag on a line, or null. */
export function firstTimestampOnLine(line: string): number | null {
  TIME_TAG_RE.lastIndex = 0;
  const m = TIME_TAG_RE.exec(line);
  if (!m) return null;
  return parseTimestamp(m[1]!, m[2]!, m[3]);
}
