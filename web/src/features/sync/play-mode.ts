export type PlayMode = "single" | "single_loop" | "loop" | "shuffle";

const STORAGE_KEY = "yubal:play-modes";
const ORDER: PlayMode[] = ["single", "single_loop", "loop", "shuffle"];

function normalizeMode(mode: string | undefined): PlayMode | null {
  if (mode === "list") return "single";
  if (mode && ORDER.includes(mode as PlayMode)) return mode as PlayMode;
  return null;
}

function readAll(): Record<string, PlayMode> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Record<string, string>;
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, PlayMode> = {};
    for (const [folder, mode] of Object.entries(parsed)) {
      const normalized = normalizeMode(mode);
      if (normalized) out[folder] = normalized;
    }
    return out;
  } catch {
    return {};
  }
}

function writeAll(modes: Record<string, PlayMode>): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(modes));
  } catch {
    // ignore
  }
}

export function getPlayMode(folder: string): PlayMode {
  const mode = normalizeMode(readAll()[folder]);
  return mode ?? "single";
}

export function setPlayMode(folder: string, mode: PlayMode): void {
  const modes = readAll();
  modes[folder] = mode;
  writeAll(modes);
}

export function cyclePlayMode(current: PlayMode): PlayMode {
  const idx = ORDER.indexOf(current);
  return ORDER[(idx + 1) % ORDER.length] ?? "single";
}
