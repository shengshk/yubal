import { UrlInput } from "@/components/common/url-input";
import { SubscriptionCard } from "@/features/subscriptions/subscription-card";
import { SubscriptionsTable } from "@/features/subscriptions/subscriptions-table";
import { useSubscriptions } from "@/features/subscriptions/use-subscriptions";
import {
  SubscriptionDeleteModal,
  type DeleteFileAction,
} from "@/features/sync/subscription-delete-modal";
import { useScheduleCountdown } from "@/hooks/use-schedule-countdown";
import type { Subscription } from "@/api/subscriptions";
import { clearSubscriptionOffline } from "@/api/subscriptions";
import { getSettings } from "@/api/settings";
import { cardActionClass, cardInputWrapper, cardShadow } from "@/lib/ui-styles";
import { isValidUrl } from "@/lib/url";
import { Alert, Card, CardBody, Input, Spinner, Tooltip } from "@heroui/react";
import {
  CircleQuestionMarkIcon,
  ClockIcon,
  HashIcon,
  ListMusicIcon,
  RefreshCw,
  ZapIcon,
  ZapOffIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const DEFAULT_MAX_ITEMS = 100;

export function SubscriptionsPage() {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const [maxItems, setMaxItems] = useState(DEFAULT_MAX_ITEMS);
  const [isAdding, setIsAdding] = useState(false);
  const [externalEnabled, setExternalEnabled] = useState(false);
  const {
    subscriptions,
    schedulerStatus,
    isLoading,
    addSubscription,
    updateSubscription,
    deleteSubscription,
    syncSubscription,
    syncAll,
    refreshScheduler,
  } = useSubscriptions();
  const [isSyncing, setIsSyncing] = useState(false);
  const [deleting, setDeleting] = useState<Subscription | null>(null);

  const canAdd = isValidUrl(url);
  const isEmpty = subscriptions.length == 0;
  const canSyncAll = !isEmpty && !isSyncing && !isLoading;

  const handleAdd = async () => {
    if (!canAdd) return;
    setIsAdding(true);
    const success = await addSubscription(url.trim(), maxItems);
    if (success) {
      setUrl("");
    }
    setIsAdding(false);
  };

  const handleToggleEnabled = async (id: string, enabled: boolean) => {
    await updateSubscription(id, { enabled });
  };

  const handleSaveFolder = async (
    id: string,
    saveFolder: string,
    confirm = false,
  ) => {
    return updateSubscription(id, {
      save_folder: saveFolder,
      confirm_folder_move: confirm,
    });
  };

  const handleSyncAll = async () => {
    setIsSyncing(true);
    await syncAll();
    setIsSyncing(false);
  };

  const handleDeleteConfirm = async (action: DeleteFileAction) => {
    if (!deleting) return false;
    if (
      action === "clear_offline_delete" ||
      action === "clear_offline_to_raw_delete"
    ) {
      const result = await clearSubscriptionOffline(
        deleting.id,
        action === "clear_offline_to_raw_delete" ? "to_raw_delete" : "delete",
      );
      return Boolean(result);
    }
    return deleteSubscription(deleting.id, action);
  };

  useEffect(() => {
    void getSettings().then((s) => {
      setExternalEnabled(Boolean(s?.external_library_enabled));
    });
  }, []);

  const countdown = useScheduleCountdown(
    schedulerStatus?.next_run_at,
    () => {
      void refreshScheduler();
    },
  );
  const nextTitle = schedulerStatus?.next_run_subscription_name?.trim() || null;
  const nextUpdateLabel =
    countdown === "—"
      ? t("playlists.nextUpdate")
      : `${t("playlists.nextUpdate")}：${countdown}${t("playlists.remaining")}`;
  const enabledCount = subscriptions.filter((s) => s.enabled).length;
  const totalCount = subscriptions.length;

  return (
    <>
      {/* Page Title */}
      <h1 className="text-foreground mb-6 text-2xl font-bold">
        {t("playlists.title")}
      </h1>

      {/* URL Input Section */}
      <section className="mb-8 flex w-full min-w-0 flex-row items-center gap-2 sm:gap-3">
        <div className="min-w-0 flex-1">
          <UrlInput
            value={url}
            onChange={setUrl}
            disabled={isAdding}
            placeholder={t("playlists.urlPlaceholder")}
          />
        </div>
        <Tooltip content={t("playlists.maxSyncTooltip")} offset={14}>
          <Input
            type="number"
            variant="flat"
            value={String(maxItems)}
            onChange={(e) => {
              const value = parseInt(e.target.value, 10);
              if (!Number.isNaN(value) && value >= 1) setMaxItems(value);
            }}
            min={1}
            max={10000}
            radius="lg"
            placeholder={t("common.max")}
            startContent={<HashIcon className="text-foreground-400 h-4 w-4" />}
            classNames={{
              base: "w-20 shrink-0 sm:w-24",
              input: "font-mono",
              inputWrapper: cardInputWrapper,
            }}
          />
        </Tooltip>
        <Card
          shadow={cardShadow}
          isHoverable={!isAdding}
          isPressable={canAdd && !isAdding}
          onPress={() => {
            if (!canAdd || isAdding) return;
            void handleAdd();
          }}
          className={`${cardActionClass} shrink-0`}
        >
          <CardBody className="text-inherit flex flex-row items-center justify-center gap-2 px-3 py-0 sm:px-4">
            {isAdding ? (
              <Spinner size="sm" color="current" />
            ) : (
              <ZapIcon className="h-4 w-4 shrink-0" />
            )}
            <span className="truncate text-sm font-medium">
              {t("playlists.subscribe")}
            </span>
          </CardBody>
        </Card>
      </section>

      {/* Stats Cards */}
      <div className="mb-6 grid w-full grid-cols-3 gap-2 md:gap-4">
        {/* Active playlists */}
        <SubscriptionCard isDisabled={!schedulerStatus?.enabled}>
          <SubscriptionCard.Header title={t("playlists.active")}>
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
        {/* Next update: time + soonest subscription title */}
        <SubscriptionCard isDisabled={!schedulerStatus?.enabled}>
          <SubscriptionCard.Header title={nextUpdateLabel}>
            <SubscriptionCard.Value>{nextTitle ?? "—"}</SubscriptionCard.Value>
          </SubscriptionCard.Header>
          <SubscriptionCard.Icon>
            <ClockIcon />
          </SubscriptionCard.Icon>
        </SubscriptionCard>
        {/* Sync all button */}
        <Card
          shadow="sm"
          isHoverable={canSyncAll}
          isPressable={canSyncAll}
          isDisabled={!canSyncAll}
          onPress={handleSyncAll}
          classNames={{
            base: "group",
            body: "flex flex-1 flex-col items-center justify-center gap-2",
          }}
        >
          <CardBody>
            <RefreshCw
              size={24}
              className={`mb-1 ${isSyncing ? "text-success-400 animate-spin" : "transition-transform duration-500 group-data-[hover=true]:rotate-180"}`}
            />
            <span className="text-small font-medium">
              {isSyncing
                ? t("playlists.synchronizing")
                : t("playlists.syncAll")}
            </span>
          </CardBody>
        </Card>
      </div>
      {/* Scheduler disabled alert */}
      {schedulerStatus?.enabled === false && (
        <div className="mb-6 flex w-full items-center justify-center">
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
      {/* Subscriptions Table */}
      <SubscriptionsTable
        subscriptions={subscriptions}
        isLoading={isLoading}
        isSchedulerEnabled={schedulerStatus?.enabled}
        onToggleEnabled={handleToggleEnabled}
        onSaveFolder={handleSaveFolder}
        onSync={syncSubscription}
        onDelete={(id) => {
          const sub = subscriptions.find((item) => item.id === id) ?? null;
          setDeleting(sub);
        }}
      />
      <SubscriptionDeleteModal
        subscription={deleting}
        isOpen={deleting !== null}
        externalEnabled={externalEnabled}
        onClose={() => setDeleting(null)}
        onConfirm={handleDeleteConfirm}
      />
    </>
  );
}
