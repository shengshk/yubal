import type { ExternalPlaylist } from "@/api/external";
import type { Job } from "@/api/jobs";
import type { Subscription } from "@/api/subscriptions";
import type { SyncLedgerEntry } from "@/api/sync-ledger";
import type { WantedSummary } from "@/api/wanted";
import { EmptyState } from "@/components/common/empty-state";
import { ExternalPlaylistCard } from "@/features/sync/external-playlist-card";
import { LedgerCard } from "@/features/sync/ledger-card";
import { PlaylistTitleTooltip } from "@/features/sync/playlist-title-tooltip";
import { WantedCard } from "@/features/sync/wanted-card";
import { externalPlaylistPriority } from "@/lib/playlist-labels";
import { isLikedMusicUrl } from "@/lib/subscription-labels";
import { layout } from "@/lib/ui-styles";
import { Button, Card, CardBody } from "@heroui/react";
import { HeartIcon, InboxIcon, LibraryIcon, ThumbsUpIcon } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export const WANTED_EXPAND_KEY = "wanted";

type Props = {
  entries: SyncLedgerEntry[];
  subscriptions: Subscription[];
  jobs: Job[];
  isLoading?: boolean;
  subscriptionsLoading?: boolean;
  onCancel?: (jobId: string) => void;
  onEdit?: (subscription: Subscription) => void;
  onSync?: (subscriptionId: string) => void;
  onDelete?: (subscription: Subscription) => void;
  onEditDirect?: () => void;
  onDeleteDirect?: () => void;
  onSyncDirect?: () => void;
  onDirectTrackDeleted?: (entry: SyncLedgerEntry) => void;
  schedulerEnabled?: boolean;
  expandedKey: string | null;
  onExpandedKeyChange: (key: string | null) => void;
  externalPlaylists?: ExternalPlaylist[];
  showExternalSection?: boolean;
  externalLoading?: boolean;
  onEditExternal?: (playlist: ExternalPlaylist) => void;
  onDeleteExternal?: (playlist: ExternalPlaylist) => void;
  onExternalChanged?: () => void;
  onActivateExternalPending?: (mode: "readonly" | "managed") => void;
  wantedSummary?: WantedSummary | null;
  showWantedSection?: boolean;
};

function matchJob(entry: SyncLedgerEntry, jobs: Job[]): Job | undefined {
  const active = jobs.filter(
    (j) =>
      j.status === "pending" ||
      j.status === "fetching_info" ||
      j.status === "downloading" ||
      j.status === "importing",
  );

  if (entry.kind === "direct") {
    return active.find((j) => !j.subscription_id);
  }
  if (entry.subscription_id) {
    return active.find((j) => j.subscription_id === entry.subscription_id);
  }
  return undefined;
}

function syntheticEntry(sub: Subscription): SyncLedgerEntry {
  return {
    id: sub.id,
    key: `subscription:${sub.id}`,
    kind: "subscription",
    subscription_id: sub.id,
    save_folder: sub.save_folder,
    title: sub.name,
    thumbnail_url: sub.thumbnail_url ?? null,
    content_kind: "playlist",
    url: sub.url,
    total_count: 0,
    synced_count: 0,
    real_download_count: 0,
    hardlink_count: 0,
    failed_count: 0,
    skipped_ugc: 0,
    skipped_region: 0,
    skipped_other: 0,
    last_job_id: null,
    last_job_status: null,
    last_synced_at: sub.last_synced_at,
    updated_at: sub.created_at,
  };
}

function syntheticDirectEntry(): SyncLedgerEntry {
  return {
    id: "direct",
    key: "direct",
    kind: "direct",
    subscription_id: null,
    save_folder: "direct",
    title: "direct",
    thumbnail_url: null,
    content_kind: "playlist",
    url: null,
    total_count: 0,
    synced_count: 0,
    real_download_count: 0,
    hardlink_count: 0,
    failed_count: 0,
    skipped_ugc: 0,
    skipped_region: 0,
    skipped_other: 0,
    last_job_id: null,
    last_job_status: null,
    last_synced_at: null,
    updated_at: new Date().toISOString(),
  };
}

type Row = {
  entry: SyncLedgerEntry;
  subscription: Subscription | null;
};

function Section({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={`flex flex-col ${layout.sectionInner}`}>
      <div className="flex min-h-8 items-center justify-between gap-3">
        <h2 className={layout.sectionTitle}>{title}</h2>
        {actions}
      </div>
      {children}
    </div>
  );
}

function SystemPlaylistPlaceholder({
  kind,
  title,
  status,
}: {
  kind: "wanted" | "liked";
  title: string;
  status: string;
}) {
  const { t } = useTranslation();
  const Icon = kind === "wanted" ? HeartIcon : ThumbsUpIcon;
  const path = kind === "wanted" ? "wanted/" : "download/liked";
  return (
    <Card shadow="sm" className="bg-content1 overflow-hidden">
      <CardBody className="flex h-20 max-h-20 min-h-20 flex-row items-center gap-3 overflow-hidden p-0">
        <div className="bg-default-100 m-3 flex h-14 w-14 shrink-0 items-center justify-center rounded-md">
          <Icon className="text-foreground-400 h-6 w-6" />
        </div>
        <div className="max-h-full min-w-0 flex-1 overflow-hidden py-2">
          <PlaylistTitleTooltip
            kind={kind}
            className="text-foreground block min-w-0 truncate text-sm font-medium"
          >
            {title}
          </PlaylistTitleTooltip>
          <p className="text-foreground-500 mt-1 truncate font-mono text-xs">
            {status}
          </p>
          <p className="text-foreground-400 mt-1 truncate font-mono text-xs">
            {t("sync.fixedPathLabel", { path })}
          </p>
        </div>
      </CardBody>
    </Card>
  );
}

export function LedgerPanel({
  entries,
  subscriptions,
  jobs,
  isLoading,
  subscriptionsLoading = false,
  onCancel,
  onEdit,
  onSync,
  onDelete,
  onEditDirect,
  onDeleteDirect,
  onSyncDirect,
  onDirectTrackDeleted,
  schedulerEnabled = true,
  expandedKey,
  onExpandedKeyChange,
  externalPlaylists = [],
  showExternalSection = false,
  externalLoading = false,
  onEditExternal,
  onDeleteExternal,
  onExternalChanged,
  onActivateExternalPending,
  wantedSummary = null,
  showWantedSection = false,
}: Props) {
  const { t } = useTranslation();

  const toggleTracks = (key: string) => {
    onExpandedKeyChange(expandedKey === key ? null : key);
  };

  const collapseTracks = () => onExpandedKeyChange(null);

  const { directRow, likedRow, subscriptionRows } = useMemo(() => {
    const ledgerBySub = new Map(
      entries
        .filter((e) => e.kind === "subscription" && e.subscription_id)
        .map((e) => [e.subscription_id as string, e]),
    );
    const direct = entries.find((e) => e.kind === "direct");
    const orphanJob = jobs.find(
      (j) =>
        !j.subscription_id &&
        (j.status === "pending" ||
          j.status === "fetching_info" ||
          j.status === "downloading" ||
          j.status === "importing") &&
        !direct,
    );
    const directEntry = direct
      ? direct
      : orphanJob
        ? {
            id: orphanJob.id,
            key: `job:${orphanJob.id}`,
            kind: "direct" as const,
            subscription_id: null,
            save_folder: "direct",
            title: orphanJob.content_info?.title || orphanJob.url || "direct",
            thumbnail_url: orphanJob.content_info?.thumbnail_url ?? null,
            content_kind: orphanJob.content_info?.kind ?? "playlist",
            url: orphanJob.url ?? null,
            total_count: orphanJob.content_info?.track_count ?? 0,
            synced_count: 0,
            real_download_count: 0,
            hardlink_count: 0,
            failed_count: 0,
            skipped_ugc: 0,
            skipped_region: 0,
            skipped_other: 0,
            last_job_id: orphanJob.id,
            last_job_status: orphanJob.status,
            last_synced_at: null,
            updated_at: orphanJob.created_at ?? new Date().toISOString(),
          }
        : syntheticDirectEntry();
    const directRow: Row = {
      entry: directEntry,
      subscription: null,
    };

    const rows: Row[] = subscriptions.map((sub) => ({
      entry: ledgerBySub.get(sub.id) ?? syntheticEntry(sub),
      subscription: sub,
    }));
    const likedRow =
      rows.find((row) => isLikedMusicUrl(row.subscription?.url)) ?? null;
    const subscriptionRows = rows.filter(
      (row) => !isLikedMusicUrl(row.subscription?.url),
    );

    return { directRow, likedRow, subscriptionRows };
  }, [entries, subscriptions, jobs]);

  // Hide empty external playlists (no unmatched + no matched tracks).
  const visibleExternalPlaylists = useMemo(
    () =>
      externalPlaylists
        .map((playlist, index) => ({ playlist, index }))
        .sort(
          (a, b) =>
            externalPlaylistPriority(
              a.playlist.dir_name,
              a.playlist.access_mode,
            ) -
              externalPlaylistPriority(
                b.playlist.dir_name,
                b.playlist.access_mode,
              ) || a.index - b.index,
        )
        .map(({ playlist }) => playlist),
    [externalPlaylists],
  );

  if (isLoading) {
    return (
      <p className="text-foreground-400 font-mono text-sm">
        {t("common.loading")}
      </p>
    );
  }

  return (
    <div className={`flex flex-col ${layout.blockGap}`}>
      <Section title={t("sync.sectionSystemPlaylists")}>
        <div className={`flex flex-col ${layout.sectionInner}`}>
          <LedgerCard
            key={directRow.entry.key}
            entry={directRow.entry}
            subscription={null}
            subscriptions={subscriptions}
            activeJob={matchJob(directRow.entry, jobs)}
            tracksOpen={expandedKey === directRow.entry.key}
            onToggleTracks={() => toggleTracks(directRow.entry.key)}
            onCollapseTracks={collapseTracks}
            onDirectTrackDeleted={onDirectTrackDeleted}
            onCancel={onCancel}
            onEditDirect={onEditDirect}
            onDeleteDirect={onDeleteDirect}
            onSyncDirect={onSyncDirect}
            schedulerEnabled={schedulerEnabled}
          />

          {showWantedSection && wantedSummary ? (
            <WantedCard
              summary={wantedSummary}
              tracksOpen={expandedKey === WANTED_EXPAND_KEY}
              onToggleTracks={() => toggleTracks(WANTED_EXPAND_KEY)}
              onCollapseTracks={collapseTracks}
              likedSubscription={likedRow?.subscription}
              likedEntry={likedRow?.entry}
              onSyncLiked={onSync}
            />
          ) : likedRow?.subscription ? (
            <LedgerCard
              key={likedRow.entry.key}
              entry={likedRow.entry}
              subscription={likedRow.subscription}
              subscriptions={subscriptions}
              activeJob={matchJob(likedRow.entry, jobs)}
              tracksOpen={expandedKey === likedRow.entry.key}
              onToggleTracks={() => toggleTracks(likedRow.entry.key)}
              onCollapseTracks={collapseTracks}
              onCancel={onCancel}
              onEdit={onEdit}
              onSync={onSync}
              onDelete={onDelete}
              schedulerEnabled={schedulerEnabled}
            />
          ) : (
            <SystemPlaylistPlaceholder
              kind="wanted"
              title={t("sync.favoriteCardTitle")}
              status={t("sync.systemPlaylistDisabled")}
            />
          )}
        </div>
      </Section>

      <Section title={t("sync.sectionSubscriptions")}>
        {subscriptionsLoading ? (
          <Card shadow="sm" className="bg-content1 h-20 animate-pulse" />
        ) : subscriptionRows.length === 0 ? (
          <EmptyState icon={InboxIcon} title={t("sync.emptySubscriptions")} />
        ) : (
          <div className={`flex flex-col ${layout.sectionInner}`}>
            {subscriptionRows.map((row) => (
              <LedgerCard
                key={row.entry.key}
                entry={row.entry}
                subscription={row.subscription}
                subscriptions={subscriptions}
                activeJob={matchJob(row.entry, jobs)}
                tracksOpen={expandedKey === row.entry.key}
                onToggleTracks={() => toggleTracks(row.entry.key)}
                onCollapseTracks={collapseTracks}
                onCancel={onCancel}
                onEdit={onEdit}
                onSync={onSync}
                onDelete={onDelete}
                schedulerEnabled={schedulerEnabled}
              />
            ))}
          </div>
        )}
      </Section>

      {showExternalSection ? (
        <Section
          title={t("sync.sectionExternal")}
          actions={
            externalPlaylists.some((p) => p.access_mode === "pending") ? (
              <div className="flex shrink-0 items-center gap-2">
                <Button
                  size="sm"
                  variant="flat"
                  className="bg-default-100 text-foreground w-36 justify-center"
                  onPress={() => onActivateExternalPending?.("readonly")}
                >
                  {t("sync.externalActivateReadonly")}
                </Button>
                <Button
                  size="sm"
                  variant="flat"
                  className="bg-default-100 text-foreground w-36 justify-center"
                  onPress={() => onActivateExternalPending?.("managed")}
                >
                  {t("sync.externalActivateManaged")}
                </Button>
              </div>
            ) : undefined
          }
        >
          {externalLoading ? (
            <div className={`flex flex-col ${layout.sectionInner}`}>
              {[0, 1].map((key) => (
                <Card
                  key={key}
                  shadow="sm"
                  className="bg-content1 h-20 animate-pulse"
                />
              ))}
            </div>
          ) : visibleExternalPlaylists.length === 0 ? (
            <EmptyState icon={LibraryIcon} title={t("sync.emptyExternal")} />
          ) : (
            <div className={`flex flex-col ${layout.sectionInner}`}>
              {visibleExternalPlaylists.map((playlist) => {
                const key = `external:${playlist.dir_name}`;
                return (
                  <ExternalPlaylistCard
                    key={key}
                    playlist={playlist}
                    tracksOpen={expandedKey === key}
                    schedulerEnabled={schedulerEnabled}
                    onToggleTracks={() => toggleTracks(key)}
                    onCollapseTracks={collapseTracks}
                    onEdit={(p) => onEditExternal?.(p)}
                    onDelete={(p) => onDeleteExternal?.(p)}
                    onChanged={() => onExternalChanged?.()}
                  />
                );
              })}
            </div>
          )}
        </Section>
      ) : null}
    </div>
  );
}
