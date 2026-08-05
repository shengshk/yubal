/** Disk dir names for the two special external salvage pits (unchanged). */
export const EXTERNAL_DELETED_DIR = "delete";
export const EXTERNAL_ARCHIVE_DIR = "default";

export type SpecialExternalPit = "deleted" | "archive";

export function specialExternalPit(dirName: string): SpecialExternalPit | null {
  if (dirName === EXTERNAL_DELETED_DIR) return "deleted";
  if (dirName === EXTERNAL_ARCHIVE_DIR) return "archive";
  return null;
}

/** UI label for an external playlist dir (special pits get friendly names). */
export function externalPlaylistDisplayName(
  dirName: string,
  t: (key: string) => string,
): string {
  const pit = specialExternalPit(dirName);
  if (pit === "deleted") return t("sync.specialPitDeleted");
  if (pit === "archive") return t("sync.specialPitArchive");
  return dirName;
}

/** Physical path hint under the library mount (relative). */
export function externalPlaylistPathHint(dirName: string): string {
  const pit = specialExternalPit(dirName);
  if (pit === "deleted") return "external/raw/delete";
  if (pit === "archive")
    return "external/raw/default · external/organized/default";
  return `external/raw/${dirName} · external/organized/${dirName}`;
}

/** Fixed product order: system → managed → read-only → pending setup. */
export function externalPlaylistPriority(
  dirName: string,
  accessMode: "pending" | "readonly" | "managed" = "readonly",
): number {
  const pit = specialExternalPit(dirName);
  if (pit === "deleted") return 0;
  if (pit === "archive") return 1;
  if (accessMode === "managed") return 2;
  if (accessMode === "readonly") return 3;
  return 4;
}
