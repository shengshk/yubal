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
import type { DirectPolicyUpdates } from "@/api/sync-ledger";

export type DirectEditValues = {
  save_folder: string;
  enabled: boolean;
  max_items: number;
  sync_jitter_seconds: number;
  offline_marking_enabled: boolean;
  offline_cleanup_enabled: boolean;
  offline_cleanup_action: "delete" | "archive";
  offline_cleanup_delay_hours: number;
};

type Props = {
  isOpen: boolean;
  currentFolder: string;
  initial?: Partial<DirectEditValues> | null;
  isSchedulerEnabled?: boolean;
  onClose: () => void;
  onSave: (
    updates: DirectPolicyUpdates & { save_folder: string },
    confirmMove: boolean,
  ) => Promise<"ok" | "folder_conflict" | "error">;
};

export function DirectEditModal({
  isOpen,
  currentFolder,
  initial,
  isSchedulerEnabled = true,
  onClose,
  onSave,
}: Props) {
  const { t } = useTranslation();
  const [folder, setFolder] = useState(currentFolder);
  const [enabled, setEnabled] = useState(false);
  const [maxItems, setMaxItems] = useState(100);
  const [jitterSeconds, setJitterSeconds] = useState(600);
  const [offlineMarking, setOfflineMarking] = useState(true);
  const [offlineCleanup, setOfflineCleanup] = useState(false);
  const [cleanupAction, setCleanupAction] = useState<"delete" | "archive">(
    "archive",
  );
  const [cleanupDelayHours, setCleanupDelayHours] = useState(72);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isOpen) return;
    setFolder(initial?.save_folder ?? currentFolder);
    setEnabled(initial?.enabled ?? false);
    setMaxItems(initial?.max_items ?? 100);
    setJitterSeconds(initial?.sync_jitter_seconds ?? 600);
    setOfflineMarking(initial?.offline_marking_enabled ?? true);
    setOfflineCleanup(initial?.offline_cleanup_enabled ?? false);
    setCleanupAction(initial?.offline_cleanup_action ?? "archive");
    setCleanupDelayHours(initial?.offline_cleanup_delay_hours ?? 72);
  }, [isOpen, currentFolder, initial]);

  const handleSave = async (confirm = false) => {
    const trimmed = folder.trim();
    if (!trimmed) return;
    setSaving(true);
    const updates = {
      save_folder: trimmed,
      enabled,
      max_items: maxItems >= 1 ? maxItems : 100,
      sync_jitter_seconds: Math.min(600, Math.max(0, jitterSeconds)),
      offline_marking_enabled: offlineMarking,
      offline_cleanup_enabled: offlineMarking && offlineCleanup,
      offline_cleanup_action: cleanupAction,
      offline_cleanup_delay_hours: Math.min(
        8760,
        Math.max(0, cleanupDelayHours),
      ),
    };
    let result = await onSave(updates, confirm);
    if (result === "folder_conflict" && !confirm) {
      const ok = window.confirm(
        t("playlists.folderConflictConfirm", { folder: trimmed }),
      );
      if (ok) result = await onSave(updates, true);
      else {
        setSaving(false);
        return;
      }
    }
    setSaving(false);
    if (result === "ok") onClose();
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
        <ModalHeader>{t("sync.editDirectTitle")}</ModalHeader>
        <ModalBody className="gap-4">
          <FolderPicker
            label={t("playlists.columns.saveFolder")}
            value={folder}
            onChange={setFolder}
          />

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.directAutoRecover")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.directAutoRecoverHint")}
              </p>
            </div>
            <Switch
              isSelected={enabled}
              isDisabled={!isSchedulerEnabled}
              onValueChange={setEnabled}
              aria-label={t("sync.directAutoRecover")}
            />
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.directOfflineMarking")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.directOfflineMarkingHint")}
              </p>
            </div>
            <Switch
              isSelected={offlineMarking}
              onValueChange={setOfflineMarking}
              aria-label={t("sync.directOfflineMarking")}
            />
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.idInvalidCleanup")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.idInvalidCleanupHint")}
              </p>
            </div>
            <Switch
              isSelected={offlineCleanup}
              isDisabled={!offlineMarking}
              onValueChange={setOfflineCleanup}
              aria-label={t("sync.idInvalidCleanup")}
            />
          </div>
          {offlineCleanup ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Select
                label={t("sync.idInvalidCleanupAction")}
                selectedKeys={[cleanupAction]}
                onSelectionChange={(keys) => {
                  const value = Array.from(keys)[0];
                  if (value === "delete" || value === "archive") {
                    setCleanupAction(value);
                  }
                }}
              >
                <SelectItem key="archive">
                  {t("sync.idInvalidActionToRawDelete")}
                </SelectItem>
                <SelectItem key="delete">
                  {t("sync.idInvalidActionDelete")}
                </SelectItem>
              </Select>
              <Input
                type="number"
                label={t("sync.idInvalidCleanupDelay")}
                description={t("sync.idInvalidCleanupDelayHint")}
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
        </ModalBody>
        <ModalFooter>
          <Button variant="light" onPress={onClose} isDisabled={saving}>
            {t("sync.cancel")}
          </Button>
          <Button
            color="primary"
            isLoading={saving}
            isDisabled={!folder.trim()}
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
