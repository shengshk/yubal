import { SubscriptionCard } from "@/features/subscriptions/subscription-card";
import { useScheduleCountdown } from "@/hooks/use-schedule-countdown";
import type { LibraryTrackSummary } from "@/api/library";
import type { SchedulerStatus } from "@/api/subscriptions";
import type { SyncStepResult } from "@/api/subscriptions";
import { formatSmartDateTime } from "@/lib/format";
import { layout } from "@/lib/ui-styles";
import { Alert, Tooltip } from "@heroui/react";
import {
  CircleQuestionMarkIcon,
  ClockIcon,
  Disc3Icon,
  ListMusicIcon,
  RefreshCw,
  ZapOffIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

type Props = {
  librarySummary: LibraryTrackSummary | null;
  enabledCount: number;
  totalCount: number;
  schedulerStatus: SchedulerStatus | null;
  lastDataUpdatedAt: string | null;
  isSyncing: boolean;
  syncSteps: SyncStepResult[];
  canSyncAll: boolean;
  onSyncAll: () => void;
  onCountdownExpire?: () => void;
};

export function SchedulerBar({
  librarySummary,
  enabledCount,
  totalCount,
  schedulerStatus,
  lastDataUpdatedAt,
  isSyncing,
  syncSteps,
  canSyncAll,
  onSyncAll,
  onCountdownExpire,
}: Props) {
  const { t } = useTranslation();
  const countdown = useScheduleCountdown(
    schedulerStatus?.next_run_at,
    onCountdownExpire,
  );
  const schedulerOn = schedulerStatus?.enabled !== false;
  const nextTitle = schedulerStatus?.next_run_subscription_name?.trim() || null;
  const nextUpdateLabel =
    countdown === "—"
      ? t("playlists.nextUpdate")
      : `${t("playlists.nextUpdate")}：${countdown}${t("playlists.remaining")}`;

  return (
    <div className={`${layout.blockMargin} flex flex-col gap-4`}>
      <div className="grid w-full grid-cols-2 gap-2 md:gap-4 xl:grid-cols-4">
        <Tooltip
          placement="bottom-start"
          content={
            <div className="max-w-[18rem] space-y-1 text-xs leading-relaxed">
              <p>{t("playlists.librarySummaryTipScope")}</p>
              <p>{t("playlists.librarySummaryTipRule")}</p>
            </div>
          }
          classNames={{ content: "px-3 py-2" }}
        >
          <SubscriptionCard>
            <SubscriptionCard.Header
              title={t("playlists.effectiveTrackTotal")}
              className="max-w-full"
            >
              <SubscriptionCard.Value className="text-xs">
                {librarySummary
                  ? t("playlists.libraryTrackBreakdown", {
                      effective: librarySummary.effective_count,
                      identified: librarySummary.identified_count,
                      unidentified: librarySummary.unidentified_count,
                      verified: librarySummary.verified_count,
                      unverified: librarySummary.unverified_count,
                    })
                  : "—"}
              </SubscriptionCard.Value>
            </SubscriptionCard.Header>
            <SubscriptionCard.Icon className="bg-primary/10 text-primary">
              <Disc3Icon />
            </SubscriptionCard.Icon>
          </SubscriptionCard>
        </Tooltip>

        <Tooltip
          placement="bottom"
          content={
            <p className="max-w-[18rem] text-xs leading-relaxed">
              {t("playlists.subscriptionCountTip")}
            </p>
          }
          classNames={{ content: "px-3 py-2" }}
        >
          <div className="h-full w-full">
            <SubscriptionCard className="h-full w-full" isDisabled={!schedulerOn}>
              <SubscriptionCard.Header title={t("playlists.subscriptionCount")}>
                <SubscriptionCard.Value
                  suffix={t("playlists.ofTotal", { count: totalCount })}
                >
                  {enabledCount}
                </SubscriptionCard.Value>
              </SubscriptionCard.Header>
              <SubscriptionCard.Icon className="text-success bg-success/10">
                <ListMusicIcon />
              </SubscriptionCard.Icon>
            </SubscriptionCard>
          </div>
        </Tooltip>

        <Tooltip
          placement="bottom"
          content={
            <p className="max-w-[18rem] text-xs leading-relaxed">
              {t(
                schedulerOn
                  ? "playlists.nextUpdateTip"
                  : "playlists.lastDataUpdateTip",
              )}
            </p>
          }
          classNames={{ content: "px-3 py-2" }}
        >
          <div className="h-full w-full">
            <SubscriptionCard className="h-full w-full" isDisabled={!schedulerOn}>
              <SubscriptionCard.Header
                title={
                  schedulerOn ? nextUpdateLabel : t("playlists.lastDataUpdate")
                }
              >
                {schedulerOn ? (
                  <SubscriptionCard.Value>
                    {nextTitle ?? "—"}
                  </SubscriptionCard.Value>
                ) : (
                  <SubscriptionCard.Value>
                    {formatSmartDateTime(lastDataUpdatedAt)}
                  </SubscriptionCard.Value>
                )}
              </SubscriptionCard.Header>
              <SubscriptionCard.Icon>
                <ClockIcon />
              </SubscriptionCard.Icon>
            </SubscriptionCard>
          </div>
        </Tooltip>

        <Tooltip
          placement="bottom-end"
          content={
            <div className="max-w-[22rem] space-y-1.5 text-xs leading-relaxed">
              <p>{t("playlists.syncNowTipIntro")}</p>
              <ol className="list-decimal space-y-1 pl-4">
                {Array.from({ length: 7 }, (_, index) => (
                  <li key={index}>
                    {t(`playlists.syncNowTip${index + 1}`)}
                    {syncSteps[index] ? (
                      <span className="text-foreground-400">
                        {" · "}
                        {t(
                          `playlists.syncStepStatus.${syncSteps[index].status}`,
                          { count: syncSteps[index].count ?? 0 },
                        )}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ol>
            </div>
          }
          classNames={{ content: "px-3 py-2.5" }}
        >
          <div className="h-full w-full">
            <SubscriptionCard
              isHoverable={canSyncAll}
              isPressable={canSyncAll}
              isDisabled={!canSyncAll}
              onPress={onSyncAll}
              className="group h-full w-full"
            >
              <SubscriptionCard.Header title={t("playlists.syncNowTitle")}>
                <SubscriptionCard.Value>
                  {isSyncing
                    ? t("playlists.synchronizing")
                    : t("playlists.syncAllData")}
                </SubscriptionCard.Value>
              </SubscriptionCard.Header>
              <SubscriptionCard.Icon className="bg-primary/10 text-primary">
                <RefreshCw
                  className={
                    isSyncing
                      ? "animate-spin"
                      : "transition-transform duration-500 group-data-[hover=true]:rotate-180"
                  }
                />
              </SubscriptionCard.Icon>
            </SubscriptionCard>
          </div>
        </Tooltip>
      </div>

      {schedulerStatus?.enabled === false && (
        <div className="flex w-full items-center justify-center">
          <Alert
            icon={<ZapOffIcon size={18} />}
            endContent={
              <a
                target="_blank"
                rel="noopener noreferrer"
                href="https://github.com/guillevc/yubal?tab=readme-ov-file#%EF%B8%8F-configuration"
              >
                <CircleQuestionMarkIcon size={20} className="mr-2" />
              </a>
            }
            color="warning"
            title={t("playlists.schedulerDisabledTitle")}
            description={t("playlists.schedulerDisabledDesc")}
          />
        </div>
      )}
    </div>
  );
}
