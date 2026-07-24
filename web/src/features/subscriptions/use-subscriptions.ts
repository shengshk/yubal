import {
  addSubscription as addSubscriptionApi,
  deleteSubscription as deleteSubscriptionApi,
  getStatus,
  listSubscriptions,
  syncAll as syncAllApi,
  syncSubscription as syncSubscriptionApi,
  updateSubscription as updateSubscriptionApi,
  type SchedulerStatus,
  type Subscription,
} from "@/api/subscriptions";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export interface UseSubscriptionsResult {
  subscriptions: Subscription[];
  schedulerStatus: SchedulerStatus | null;
  isLoading: boolean;
  refresh: () => Promise<void>;
  refreshScheduler: () => Promise<void>;
  addSubscription: (url: string, maxItems?: number) => Promise<boolean>;
  updateSubscription: (
    id: string,
    updates: {
      enabled?: boolean;
      save_folder?: string;
      max_items?: number | null;
      sync_jitter_seconds?: number;
      sync_mode?: "incremental" | "mirror";
      offline_marking_enabled?: boolean;
      offline_cleanup_enabled?: boolean;
      offline_cleanup_action?: "delete" | "archive";
      offline_cleanup_delay_hours?: number;
      confirm_folder_move?: boolean;
    },
  ) => Promise<"ok" | "folder_conflict" | "error">;
  deleteSubscription: (
    id: string,
    fileAction?: "keep" | "keep_list" | "delete" | "move_to_direct",
  ) => Promise<boolean>;
  syncSubscription: (id: string) => Promise<void>;
  syncAll: () => Promise<void>;
}

export function useSubscriptions(): UseSubscriptionsResult {
  const { t } = useTranslation();
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [schedulerStatus, setSchedulerStatus] =
    useState<SchedulerStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchSubscriptions = useCallback(async () => {
    const data = await listSubscriptions();
    setSubscriptions(data);
  }, []);

  const refresh = useCallback(async () => {
    const [subscriptionsData, statusData] = await Promise.all([
      listSubscriptions(),
      getStatus(),
    ]);
    setSubscriptions(subscriptionsData);
    setSchedulerStatus(statusData);
  }, []);

  const refreshScheduler = useCallback(async () => {
    const statusData = await getStatus();
    setSchedulerStatus(statusData);
  }, []);

  const addSubscription = useCallback(
    async (url: string, maxItems?: number): Promise<boolean> => {
      const result = await addSubscriptionApi(url, maxItems);
      if (!result.success) {
        showErrorToast(t("subscriptions.addFailedTitle"), result.error);
        return false;
      }
      await fetchSubscriptions();
      return true;
    },
    [fetchSubscriptions, t],
  );

  const updateSubscription = useCallback(
    async (
      id: string,
      updates: {
        enabled?: boolean;
        save_folder?: string;
        max_items?: number | null;
        sync_jitter_seconds?: number;
        sync_mode?: "incremental" | "mirror";
        offline_marking_enabled?: boolean;
        offline_cleanup_enabled?: boolean;
        offline_cleanup_action?: "delete" | "archive";
        offline_cleanup_delay_hours?: number;
        confirm_folder_move?: boolean;
      },
    ): Promise<"ok" | "folder_conflict" | "error"> => {
      const result = await updateSubscriptionApi(id, updates);
      if (!result.success) {
        if (result.folderConflict) {
          return "folder_conflict";
        }
        showErrorToast(
          t("subscriptions.updateFailedTitle"),
          result.error || t("subscriptions.updateFailedDesc"),
        );
        return "error";
      }
      await fetchSubscriptions();
      return "ok";
    },
    [fetchSubscriptions, t],
  );

  const deleteSubscription = useCallback(
    async (
      id: string,
      fileAction: "keep" | "keep_list" | "delete" | "move_to_direct" = "keep",
    ): Promise<boolean> => {
      const success = await deleteSubscriptionApi(id, fileAction);
      if (!success) {
        showErrorToast(
          t("subscriptions.deleteFailedTitle"),
          t("subscriptions.deleteFailedDesc"),
        );
        return false;
      }
      await fetchSubscriptions();
      return true;
    },
    [fetchSubscriptions, t],
  );

  const syncSubscription = useCallback(
    async (id: string) => {
      const result = await syncSubscriptionApi(id);
      if (!result.success) {
        showErrorToast(t("subscriptions.syncFailedTitle"), result.error);
        return;
      }
      await fetchSubscriptions();
      showSuccessToast(
        t("subscriptions.syncQueuedTitle"),
        t("subscriptions.syncQueuedOne"),
      );
    },
    [fetchSubscriptions, t],
  );

  const syncAll = useCallback(async () => {
    const result = await syncAllApi();
    if (!result.success) {
      showErrorToast(t("subscriptions.syncFailedTitle"), result.error);
      return;
    }
    await fetchSubscriptions();
    showSuccessToast(
      t("subscriptions.syncQueuedTitle"),
      t("subscriptions.syncQueuedAll"),
    );
  }, [fetchSubscriptions, t]);

  useEffect(() => {
    let mounted = true;

    async function init() {
      try {
        const [subscriptionsData, statusData] = await Promise.all([
          listSubscriptions(),
          getStatus(),
        ]);
        if (mounted) {
          setSubscriptions(subscriptionsData);
          setSchedulerStatus(statusData);
        }
      } finally {
        if (mounted) setIsLoading(false);
      }
    }

    init();
    return () => {
      mounted = false;
    };
  }, []);

  return {
    subscriptions,
    schedulerStatus,
    isLoading,
    refresh,
    refreshScheduler,
    addSubscription,
    updateSubscription,
    deleteSubscription,
    syncSubscription,
    syncAll,
  };
}
