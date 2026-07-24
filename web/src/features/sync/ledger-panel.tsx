import type { ExternalPlaylist } from "@/api/external";
import type { Job } from "@/api/jobs";
import type { Subscription } from "@/api/subscriptions";
import type { SyncLedgerEntry } from "@/api/sync-ledger";
import { EmptyState } from "@/components/common/empty-state";
import { ExternalPlaylistCard } from "@/features/sync/external-playlist-card";
import { LedgerCard } from "@/features/sync/ledger-card";
import { layout } from "@/lib/ui-styles";
import { DownloadIcon, InboxIcon, LibraryIcon } from "lucide-react";
import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  entries: SyncLedgerEntry[];
  subscriptions: Subscription[];
  jobs: Job[];
  isLoading?: boolean;
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
  onEditExternal?: (playlist: ExternalPlaylist) => void;
  onDeleteExternal?: (playlist: ExternalPlaylist) => void;
  onExternalChanged?: () => void;
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

type Row = {
  entry: SyncLedgerEntry;
  subscription: Subscription | null;
};

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className={`flex flex-col ${layout.sectionInner}`}>
      <h2 className={layout.sectionTitle}>{title}</h2>
      {children}
    </div>
  );
}

export function LedgerPanel({
  entries,
  subscriptions,
  jobs,
  isLoading,
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
  onEditExternal,
  onDeleteExternal,
  onExternalChanged,
}: Props) {
  const { t } = useTranslation();

  const toggleTracks = (key: string) => {
    onExpandedKeyChange(expandedKey === key ? null : key);
  };

  const collapseTracks = () => onExpandedKeyChange(null);

  const { directRows, subscriptionRows } = useMemo(() => {
    const ledgerBySub = new Map(
      entries
        .filter((e) => e.kind === "subscription" && e.subscription_id)
        .map((e) => [e.subscription_id as string, e]),
    );
    const direct = entries.find((e) => e.kind === "direct");
    const directs: Row[] = [];

    if (direct) {
      directs.push({ entry: direct, subscription: null });
    }

    const orphanJobs = jobs.filter(
      (j) =>
        !j.subscription_id &&
        (j.status === "pending" ||
          j.status === "fetching_info" ||
          j.status === "downloading" ||
          j.status === "importing") &&
        !direct,
    );
    for (const job of orphanJobs) {
      directs.push({
        entry: {
          id: job.id,
          key: `job:${job.id}`,
          kind: "direct",
          subscription_id: null,
          save_folder: "direct",
          title: job.content_info?.title || job.url || "direct",
          thumbnail_url: job.content_info?.thumbnail_url ?? null,
          content_kind: job.content_info?.kind ?? "playlist",
          url: job.url ?? null,
          total_count: job.content_info?.track_count ?? 0,
          synced_count: 0,
          real_download_count: 0,
          hardlink_count: 0,
          failed_count: 0,
          skipped_ugc: 0,
          skipped_region: 0,
          skipped_other: 0,
          last_job_id: job.id,
          last_job_status: job.status,
          last_synced_at: null,
          updated_at: job.created_at ?? new Date().toISOString(),
        },
        subscription: null,
      });
    }

    const subs: Row[] = subscriptions.map((sub) => ({
      entry: ledgerBySub.get(sub.id) ?? syntheticEntry(sub),
      subscription: sub,
    }));

    return { directRows: directs, subscriptionRows: subs };
  }, [entries, subscriptions, jobs]);

  // Hide empty external playlists (no unmatched + no matched tracks).
  const visibleExternalPlaylists = useMemo(
    () =>
      externalPlaylists.filter(
        (p) => (p.unmatched_count ?? 0) + (p.matched_count ?? 0) > 0,
      ),
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
        <Section title={t("sync.sectionDirect")}>
          {directRows.length === 0 ? (
            <EmptyState
              icon={DownloadIcon}
              title={t("sync.emptyDirect")}
            />
          ) : (
            <div className={`flex flex-col ${layout.sectionInner}`}>
              {directRows.map((row) => (
                <LedgerCard
                  key={row.entry.key}
                  entry={row.entry}
                  subscription={row.subscription}
                  subscriptions={subscriptions}
                  activeJob={matchJob(row.entry, jobs)}
                  tracksOpen={expandedKey === row.entry.key}
                  onToggleTracks={() => toggleTracks(row.entry.key)}
                  onCollapseTracks={collapseTracks}
                  onDirectTrackDeleted={onDirectTrackDeleted}
                  onCancel={onCancel}
                  onEditDirect={onEditDirect}
                  onDeleteDirect={onDeleteDirect}
                  onSyncDirect={onSyncDirect}
                  schedulerEnabled={schedulerEnabled}
                />
              ))}
            </div>
          )}
        </Section>

        <Section title={t("sync.sectionSubscriptions")}>
          {subscriptionRows.length === 0 ? (
            <EmptyState
              icon={InboxIcon}
              title={t("sync.emptySubscriptions")}
            />
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
          <Section title={t("sync.sectionExternal")}>
            {visibleExternalPlaylists.length === 0 ? (
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
