import { SubscriptionCard } from "@/features/subscriptions/subscription-card";
import { useScheduleCountdown } from "@/hooks/use-schedule-countdown";
import type { SchedulerStatus } from "@/api/subscriptions";
import { formatSmartDateTime } from "@/lib/format";
import { layout } from "@/lib/ui-styles";
import { Alert } from "@heroui/react";
import {
  CircleQuestionMarkIcon,
  ClockIcon,
  ListMusicIcon,
  RefreshCw,
  ZapOffIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

type Props = {
  enabledCount: number;
  totalCount: number;
  schedulerStatus: SchedulerStatus | null;
  lastDataUpdatedAt: string | null;
  isSyncing: boolean;
  canSyncAll: boolean;
  onSyncAll: () => void;
  onCountdownExpire?: () => void;
};

export function SchedulerBar({
  enabledCount,
  totalCount,
  schedulerStatus,
  lastDataUpdatedAt,
  isSyncing,
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
      <div className="grid w-full grid-cols-2 gap-2 md:grid-cols-3 md:gap-4">
        <SubscriptionCard isDisabled={!schedulerOn}>
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

        <SubscriptionCard isDisabled={!schedulerOn}>
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

        <SubscriptionCard
          isHoverable={canSyncAll}
          isPressable={canSyncAll}
          isDisabled={!canSyncAll}
          onPress={onSyncAll}
          className="group"
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
