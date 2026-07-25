import {
  type IdInvalidCleanupAction,
  type NotInPlaylistCleanupAction,
  type Subscription,
  type SubscriptionUpdates,
  type SyncMode,
} from "@/api/subscriptions";
import { HoverHint } from "@/components/common/hover-hint";
import { FolderPicker } from "@/features/library/folder-picker";
import {
  isLikedMusicUrl,
  LIKED_MUSIC_SAVE_FOLDER,
} from "@/lib/subscription-labels";
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
  const [offlineAction, setOfflineAction] =
    useState<NotInPlaylistCleanupAction>("archive");
  const [offlineDelayHours, setOfflineDelayHours] = useState(72);

  const [idInvalidMarking, setIdInvalidMarking] = useState(true);
  const [idInvalidCleanup, setIdInvalidCleanup] = useState(false);
  const [idInvalidAction, setIdInvalidAction] =
    useState<IdInvalidCleanupAction>("archive");
  const [idInvalidDelayHours, setIdInvalidDelayHours] = useState(72);

  const [saving, setSaving] = useState(false);
  const isLikedMusic = isLikedMusicUrl(subscription?.url);
  const syncModeLabels: Record<SyncMode, string> = {
    incremental: t("sync.syncModeIncremental"),
    mirror: t("sync.syncModeMirror"),
  };
  const offlineActionsHint = [
    `${t("sync.idInvalidActionToRawDelete")}：${t("sync.cleanupActionArchiveHint")}`,
    `${t("sync.idInvalidActionDelete")}：${t("sync.cleanupActionDeleteHint")}`,
  ].join("\n");
  const idInvalidActionsHint = [
    offlineActionsHint,
    `${t("sync.migrateToWanted")}：${t("sync.migrateToWantedHint")}`,
  ].join("\n");

  useEffect(() => {
    if (!subscription || !isOpen) return;
    setSaveFolder(subscription.save_folder);
    setMaxItems(subscription.max_items ?? 50);
    setJitterSeconds(subscription.sync_jitter_seconds ?? 600);
    setEnabled(subscription.enabled);
    setSyncMode(subscription.sync_mode ?? "incremental");

    setOfflineMarking(subscription.offline_marking_enabled ?? true);
    setOfflineCleanup(subscription.offline_cleanup_enabled ?? false);
    setOfflineAction(
      subscription.offline_cleanup_action === "delete" ? "delete" : "archive",
    );
    setOfflineDelayHours(subscription.offline_cleanup_delay_hours ?? 72);

    setIdInvalidMarking(subscription.id_invalid_marking_enabled ?? true);
    setIdInvalidCleanup(subscription.id_invalid_cleanup_enabled ?? false);
    const idAction = subscription.id_invalid_cleanup_action;
    setIdInvalidAction(
      idAction === "delete" || idAction === "to_wanted" ? idAction : "archive",
    );
    setIdInvalidDelayHours(subscription.id_invalid_cleanup_delay_hours ?? 72);
  }, [subscription, isOpen]);

  const handleSave = async (confirm = false) => {
    if (!subscription) return;
    const trimmed = isLikedMusic ? LIKED_MUSIC_SAVE_FOLDER : saveFolder.trim();
    if (!trimmed) return;

    setSaving(true);
    const clampedJitter = Math.min(600, Math.max(0, jitterSeconds));
    const updates: SubscriptionUpdates = {
      enabled,
      max_items: maxItems >= 1 ? maxItems : 50,
      sync_jitter_seconds: clampedJitter,
      sync_mode: syncMode,
      offline_marking_enabled: offlineMarking,
      offline_cleanup_enabled: offlineMarking && offlineCleanup,
      offline_cleanup_action: offlineAction,
      offline_cleanup_delay_hours: Math.min(
        8760,
        Math.max(0, offlineDelayHours),
      ),
      id_invalid_marking_enabled: idInvalidMarking,
      id_invalid_cleanup_enabled: idInvalidMarking && idInvalidCleanup,
      id_invalid_cleanup_action: idInvalidAction,
      id_invalid_cleanup_delay_hours: Math.min(
        8760,
        Math.max(0, idInvalidDelayHours),
      ),
    };
    if (!isLikedMusic && trimmed !== subscription.save_folder) {
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
          {isLikedMusic ? (
            <HoverHint content={t("sync.likedFixedPathHint")}>
              <Input
                label={t("playlists.columns.saveFolder")}
                value={LIKED_MUSIC_SAVE_FOLDER}
                isReadOnly
              />
            </HoverHint>
          ) : (
            <FolderPicker
              label={t("playlists.columns.saveFolder")}
              value={saveFolder}
              onChange={setSaveFolder}
            />
          )}

          <HoverHint content={t("sync.itemEnableHint")}>
            <div className="flex items-center justify-between gap-3">
              <p className="text-sm font-medium">
                {t("sync.subscriptionSyncEnabled")}
              </p>
              <Switch
                isSelected={enabled}
                isDisabled={!isSchedulerEnabled}
                onValueChange={setEnabled}
                aria-label={t("playlists.toggleAutoSync")}
              />
            </div>
          </HoverHint>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <HoverHint content={t("sync.perRoundCapHint")}>
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
            </HoverHint>
            <HoverHint content={t("sync.syncJitterHint")}>
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
            </HoverHint>
            <HoverHint
              content={[
                t("sync.syncModeHint"),
                syncMode === "mirror" ? t("sync.mirrorModeHint") : "",
              ]
                .filter(Boolean)
                .join("\n")}
            >
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
            </HoverHint>
          </div>

          {syncMode === "incremental" ? (
            <>
              <div className="border-default-200 space-y-3 rounded-lg border p-3">
                <HoverHint content={t("sync.offlineMarkingHint")}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium">
                      {t("sync.offlineMarking")}
                    </p>
                    <Switch
                      isSelected={offlineMarking}
                      onValueChange={setOfflineMarking}
                      aria-label={t("sync.offlineMarking")}
                    />
                  </div>
                </HoverHint>
                <HoverHint content={t("sync.offlineCleanupHint")}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium">
                      {t("sync.offlineCleanup")}
                    </p>
                    <Switch
                      isSelected={offlineCleanup}
                      isDisabled={!offlineMarking}
                      onValueChange={setOfflineCleanup}
                      aria-label={t("sync.offlineCleanup")}
                    />
                  </div>
                </HoverHint>
                {offlineCleanup ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <HoverHint content={offlineActionsHint}>
                      <Select
                        label={t("sync.offlineCleanupAction")}
                        selectedKeys={[offlineAction]}
                        onSelectionChange={(keys) => {
                          const value = Array.from(keys)[0];
                          if (value === "delete" || value === "archive") {
                            setOfflineAction(value);
                          }
                        }}
                      >
                        <SelectItem
                          key="archive"
                          textValue={t("sync.idInvalidActionToRawDelete")}
                        >
                          {t("sync.idInvalidActionToRawDelete")}
                        </SelectItem>
                        <SelectItem
                          key="delete"
                          textValue={t("sync.idInvalidActionDelete")}
                        >
                          {t("sync.idInvalidActionDelete")}
                        </SelectItem>
                      </Select>
                    </HoverHint>
                    <HoverHint content={t("sync.offlineCleanupDelayHint")}>
                      <Input
                        type="number"
                        label={t("sync.offlineCleanupDelay")}
                        value={String(offlineDelayHours)}
                        min={0}
                        max={8760}
                        onChange={(e) => {
                          const value = parseInt(e.target.value, 10);
                          if (!Number.isNaN(value) && value >= 0) {
                            setOfflineDelayHours(value);
                          }
                        }}
                      />
                    </HoverHint>
                  </div>
                ) : null}
              </div>

              <div className="border-default-200 space-y-3 rounded-lg border p-3">
                <HoverHint content={t("sync.subIdInvalidMarkingHint")}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium">
                      {t("sync.subIdInvalidMarking")}
                    </p>
                    <Switch
                      isSelected={idInvalidMarking}
                      onValueChange={setIdInvalidMarking}
                      aria-label={t("sync.subIdInvalidMarking")}
                    />
                  </div>
                </HoverHint>
                <HoverHint content={t("sync.idInvalidCleanupHint")}>
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-medium">
                      {t("sync.idInvalidCleanup")}
                    </p>
                    <Switch
                      isSelected={idInvalidCleanup}
                      isDisabled={!idInvalidMarking}
                      onValueChange={setIdInvalidCleanup}
                      aria-label={t("sync.idInvalidCleanup")}
                    />
                  </div>
                </HoverHint>
                {idInvalidCleanup ? (
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <HoverHint content={idInvalidActionsHint}>
                      <Select
                        label={t("sync.idInvalidCleanupAction")}
                        selectedKeys={[idInvalidAction]}
                        onSelectionChange={(keys) => {
                          const value = Array.from(keys)[0];
                          if (
                            value === "delete" ||
                            value === "archive" ||
                            value === "to_wanted"
                          ) {
                            setIdInvalidAction(value);
                          }
                        }}
                      >
                        <SelectItem
                          key="archive"
                          textValue={t("sync.idInvalidActionToRawDelete")}
                        >
                          {t("sync.idInvalidActionToRawDelete")}
                        </SelectItem>
                        <SelectItem
                          key="to_wanted"
                          textValue={t("sync.migrateToWanted")}
                        >
                          {t("sync.migrateToWanted")}
                        </SelectItem>
                        <SelectItem
                          key="delete"
                          textValue={t("sync.idInvalidActionDelete")}
                        >
                          {t("sync.idInvalidActionDelete")}
                        </SelectItem>
                      </Select>
                    </HoverHint>
                    <HoverHint content={t("sync.idInvalidCleanupDelayHint")}>
                      <Input
                        type="number"
                        label={t("sync.idInvalidCleanupDelay")}
                        value={String(idInvalidDelayHours)}
                        min={0}
                        max={8760}
                        onChange={(e) => {
                          const value = parseInt(e.target.value, 10);
                          if (!Number.isNaN(value) && value >= 0) {
                            setIdInvalidDelayHours(value);
                          }
                        }}
                      />
                    </HoverHint>
                  </div>
                ) : null}
              </div>
            </>
          ) : null}
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
