import {
  PlaylistStatsLine,
  type PlaylistStatItem,
} from "@/features/sync/playlist-stats-line";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

type Detail = {
  label: string;
  value: number;
};

export type UnifiedPlaylistCounts = {
  total: number | string;
  cloud: number;
  local: number;
  pending: number | string;
  abnormal: number;
  exclusive: number;
  shared: number;
  hardlink: number;
  pendingDetails?: Detail[];
  abnormalDetails?: Detail[];
};

type Props = {
  counts: UnifiedPlaylistCounts;
  trailing?: ReactNode;
};

function detailText(details: Detail[] | undefined): string | undefined {
  if (!details?.length) return undefined;
  return details.map((item) => `${item.label} ${item.value}`).join(" · ");
}

/**
 * The single second-line model used by every persistent playlist card.
 * Labels, definitions and order stay fixed; only the supplied counts differ.
 */
export function UnifiedPlaylistStats({ counts, trailing }: Props) {
  const { t } = useTranslation();
  const items: PlaylistStatItem[] = [
    { label: t("sync.statTotal"), value: counts.total },
    { label: t("sync.statCloud"), value: counts.cloud },
    { label: t("sync.statLocal"), value: counts.local },
    {
      label: t("sync.statPending"),
      value: counts.pending,
      detail: detailText(counts.pendingDetails),
    },
    {
      label: t("sync.statAbnormal"),
      value: counts.abnormal,
      detail: detailText(counts.abnormalDetails),
    },
    { label: t("sync.statExclusive"), value: counts.exclusive },
    { label: t("sync.statShared"), value: counts.shared },
    { label: t("sync.statHardlink"), value: counts.hardlink },
  ];
  return <PlaylistStatsLine items={items} trailing={trailing} />;
}

/** Partition local files into the same three storage counters everywhere. */
export function ownershipCounts(
  local: number,
  hardlinkRaw: number,
  folderShared: boolean,
): { exclusive: number; shared: number; hardlink: number } {
  const count = Math.max(0, local);
  const hardlink = Math.min(Math.max(0, hardlinkRaw), count);
  const remaining = Math.max(0, count - hardlink);
  return folderShared
    ? { exclusive: 0, shared: remaining, hardlink }
    : { exclusive: remaining, shared: 0, hardlink };
}
