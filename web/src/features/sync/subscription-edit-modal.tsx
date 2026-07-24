import {
  type OfflineCleanupAction,
  type Subscription,
  type SubscriptionUpdates,
  type SyncMode,
} from "@/api/subscriptions";
import { FolderPicker } from "@/features/library/folder-picker";
import {
  Button,
  Input,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
  Select,
  SelectItem,
  Switch,
} from "@heroui/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

type SaveResult = "ok" | "folder_conflict" | "error";

type Props = {
  subscription: Subscription | null;
  isOpen: boolean;
  isSchedulerEnabled?: boolean;
  onClose: () => void;
  onSave: (id: string, updates: SubscriptionUpdates) => Promise<SaveResult>;
};

export function SubscriptionEditModal({
  subscription,
  isOpen,
  isSchedulerEnabled = true,
  onClose,
  onSave,
}: Props) {
  const { t } = useTranslation();
  const [saveFolder, setSaveFolder] = useState("");
  const [maxItems, setMaxItems] = useState(50);
  const [jitterSeconds, setJitterSeconds] = useState(0);
  const [enabled, setEnabled] = useState(true);
  const [syncMode, setSyncMode] = useState<SyncMode>("incremental");
  const [offlineMarking, setOfflineMarking] = useState(true);
  const [offlineCleanup, setOfflineCleanup] = useState(false);
  const [cleanupAction, setCleanupAction] =
    useState<OfflineCleanupAction>("archive");
  const [cleanupDelayHours, setCleanupDelayHours] = useState(72);
  const [saving, setSaving] = useState(false);
  const syncModeLabels: Record<SyncMode, string> = {
    incremental: t("sync.syncModeIncremental"),
    mirror: t("sync.syncModeMirror"),
  };

  useEffect(() => {
    if (!subscription || !isOpen) return;
    setSaveFolder(subscription.save_folder);
    setMaxItems(subscription.max_items ?? 50);
    setJitterSeconds(subscription.sync_jitter_seconds ?? 600);
    setEnabled(subscription.enabled);
    setSyncMode(subscription.sync_mode ?? "incremental");
    setOfflineMarking(subscription.offline_marking_enabled ?? true);
    setOfflineCleanup(subscription.offline_cleanup_enabled ?? false);
    setCleanupAction(subscription.offline_cleanup_action ?? "archive");
    setCleanupDelayHours(subscription.offline_cleanup_delay_hours ?? 72);
  }, [subscription, isOpen]);

  const handleSave = async (confirm = false) => {
    if (!subscription) return;
    const trimmed = saveFolder.trim();
    if (!trimmed) return;

    setSaving(true);
    const clampedJitter = Math.min(600, Math.max(0, jitterSeconds));
    const updates: SubscriptionUpdates = {
      enabled,
      max_items: maxItems >= 1 ? maxItems : 50,
      sync_jitter_seconds: clampedJitter,
      sync_mode: syncMode,
      offline_marking_enabled: offlineMarking,
      offline_cleanup_enabled: offlineCleanup,
      offline_cleanup_action: cleanupAction,
      offline_cleanup_delay_hours: Math.min(
        8760,
        Math.max(0, cleanupDelayHours),
      ),
    };
    if (trimmed !== subscription.save_folder) {
      updates.save_folder = trimmed;
      updates.confirm_folder_move = confirm;
    }

    let result = await onSave(subscription.id, updates);
    if (result === "folder_conflict" && !confirm) {
      const ok = window.confirm(
        t("playlists.folderConflictConfirm", { folder: trimmed }),
      );
      if (ok) {
        result = await onSave(subscription.id, {
          ...updates,
          confirm_folder_move: true,
        });
      } else {
        setSaving(false);
        return;
      }
    }
    setSaving(false);
    if (result === "ok") {
      onClose();
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      placement="center"
      size="lg"
      scrollBehavior="inside"
      classNames={{
        closeButton:
          "border-none outline-none ring-0 shadow-none hover:bg-default-100 data-[focus-visible=true]:outline-none data-[focus-visible=true]:ring-0",
      }}
    >
      <ModalContent>
        <ModalHeader>{t("sync.editSubscription")}</ModalHeader>
        <ModalBody className="gap-4">
          <FolderPicker
            label={t("playlists.columns.saveFolder")}
            value={saveFolder}
            onChange={setSaveFolder}
          />

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.subscriptionSyncEnabled")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.itemEnableHint")}
              </p>
            </div>
            <Switch
              isSelected={enabled}
              isDisabled={!isSchedulerEnabled}
              onValueChange={setEnabled}
              aria-label={t("playlists.toggleAutoSync")}
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Input
              type="number"
              label={t("sync.perRoundCap")}
              value={String(maxItems)}
              min={1}
              max={10000}
              onChange={(e) => {
                const value = parseInt(e.target.value, 10);
                if (!Number.isNaN(value) && value >= 1) setMaxItems(value);
              }}
            />
            <Input
              type="number"
              label={t("sync.syncJitter")}
              value={String(jitterSeconds)}
              min={0}
              max={600}
              onChange={(e) => {
                const value = parseInt(e.target.value, 10);
                if (Number.isNaN(value)) {
                  setJitterSeconds(0);
                  return;
                }
                setJitterSeconds(Math.min(600, Math.max(0, value)));
              }}
            />
            <Select
              label={t("sync.syncMode")}
              selectedKeys={[syncMode]}
              onSelectionChange={(keys) => {
                const value = Array.from(keys)[0];
                if (value === "incremental" || value === "mirror") {
                  setSyncMode(value);
                }
              }}
            >
              <SelectItem
                key="incremental"
                textValue={syncModeLabels.incremental}
              >
                {syncModeLabels.incremental}
              </SelectItem>
              <SelectItem key="mirror" textValue={syncModeLabels.mirror}>
                {syncModeLabels.mirror}
              </SelectItem>
            </Select>
          </div>

          {syncMode === "incremental" ? (
            <>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">
                    {t("sync.offlineMarking")}
                  </p>
                  <p className="text-foreground-400 text-xs">
                    {t("sync.offlineMarkingHint")}
                  </p>
                </div>
                <Switch
                  isSelected={offlineMarking}
                  onValueChange={setOfflineMarking}
                  aria-label={t("sync.offlineMarking")}
                />
              </div>
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium">
                    {t("sync.offlineCleanup")}
                  </p>
                  <p className="text-foreground-400 text-xs">
                    {t("sync.offlineCleanupHint")}
                  </p>
                </div>
                <Switch
                  isSelected={offlineCleanup}
                  onValueChange={setOfflineCleanup}
                  aria-label={t("sync.offlineCleanup")}
                />
              </div>
              {offlineCleanup ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Select
                    label={t("sync.offlineCleanupAction")}
                    selectedKeys={[cleanupAction]}
                    onSelectionChange={(keys) => {
                      const value = Array.from(keys)[0];
                      if (value === "delete" || value === "archive") {
                        setCleanupAction(value);
                      }
                    }}
                  >
                    <SelectItem key="archive">
                      {t("sync.offlineActionArchive")}
                    </SelectItem>
                    <SelectItem key="delete">
                      {t("sync.offlineActionDelete")}
                    </SelectItem>
                  </Select>
                  <Input
                    type="number"
                    label={t("sync.offlineCleanupDelay")}
                    value={String(cleanupDelayHours)}
                    min={0}
                    max={8760}
                    onChange={(e) => {
                      const value = parseInt(e.target.value, 10);
                      if (!Number.isNaN(value) && value >= 0) {
                        setCleanupDelayHours(value);
                      }
                    }}
                  />
                </div>
              ) : null}
            </>
          ) : (
            <p className="text-foreground-400 text-xs">
              {t("sync.mirrorModeHint")}
            </p>
          )}

        </ModalBody>
        <ModalFooter>
          <Button variant="light" onPress={onClose} isDisabled={saving}>
            {t("sync.cancel")}
          </Button>
          <Button
            color="primary"
            isLoading={saving}
            isDisabled={!saveFolder.trim()}
            onPress={() => {
              void handleSave();
            }}
          >
            {t("sync.save")}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
