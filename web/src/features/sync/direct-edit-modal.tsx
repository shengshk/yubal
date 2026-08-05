import { HoverHint } from "@/components/common/hover-hint";
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
  enabled: boolean;
  max_items: number;
  sync_jitter_seconds: number;
  offline_marking_enabled: boolean;
  offline_cleanup_enabled: boolean;
  offline_cleanup_action: "delete" | "archive" | "to_wanted";
  offline_cleanup_delay_hours: number;
};

type Props = {
  isOpen: boolean;
  initial?: Partial<DirectEditValues> | null;
  isSchedulerEnabled?: boolean;
  onClose: () => void;
  onSave: (
    updates: DirectPolicyUpdates,
  ) => Promise<"ok" | "folder_conflict" | "error">;
};

export function DirectEditModal({
  isOpen,
  initial,
  isSchedulerEnabled = true,
  onClose,
  onSave,
}: Props) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [maxItems, setMaxItems] = useState(100);
  const [jitterSeconds, setJitterSeconds] = useState(600);
  const [offlineMarking, setOfflineMarking] = useState(true);
  const [offlineCleanup, setOfflineCleanup] = useState(false);
  const [cleanupAction, setCleanupAction] = useState<
    "delete" | "archive" | "to_wanted"
  >("archive");
  const [cleanupDelayHours, setCleanupDelayHours] = useState(72);
  const [saving, setSaving] = useState(false);
  const cleanupActionsHint = [
    `${t("sync.idInvalidActionToRawDelete")}：${t("sync.cleanupActionArchiveHint")}`,
    `${t("sync.migrateToWanted")}：${t("sync.migrateToWantedHint")}`,
    `${t("sync.idInvalidActionDelete")}：${t("sync.cleanupActionDeleteHint")}`,
  ].join("\n");

  useEffect(() => {
    if (!isOpen) return;
    setEnabled(initial?.enabled ?? false);
    setMaxItems(initial?.max_items ?? 100);
    setJitterSeconds(initial?.sync_jitter_seconds ?? 600);
    setOfflineMarking(initial?.offline_marking_enabled ?? true);
    setOfflineCleanup(initial?.offline_cleanup_enabled ?? false);
    setCleanupAction(initial?.offline_cleanup_action ?? "archive");
    setCleanupDelayHours(initial?.offline_cleanup_delay_hours ?? 72);
  }, [isOpen, initial]);

  const handleSave = async () => {
    setSaving(true);
    const updates = {
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
    try {
      const result = await onSave(updates);
      if (result === "ok") onClose();
    } finally {
      setSaving(false);
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
          <ModalHeader>{t("sync.editDirectTitle")}</ModalHeader>
          <ModalBody className="gap-4">
            <HoverHint content={t("sync.directAutoRecoverHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.directAutoRecover")}
                </p>
                <Switch
                  isSelected={enabled}
                  isDisabled={!isSchedulerEnabled}
                  onValueChange={setEnabled}
                  aria-label={t("sync.directAutoRecover")}
                />
              </div>
            </HoverHint>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
            </div>

            <HoverHint content={t("sync.directOfflineMarkingHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.directOfflineMarking")}
                </p>
                <Switch
                  isSelected={offlineMarking}
                  onValueChange={setOfflineMarking}
                  aria-label={t("sync.directOfflineMarking")}
                />
              </div>
            </HoverHint>

            <HoverHint content={t("sync.idInvalidCleanupHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.idInvalidCleanup")}
                </p>
                <Switch
                  isSelected={offlineCleanup}
                  isDisabled={!offlineMarking}
                  onValueChange={setOfflineCleanup}
                  aria-label={t("sync.idInvalidCleanup")}
                />
              </div>
            </HoverHint>
            {offlineCleanup ? (
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <HoverHint content={cleanupActionsHint}>
                  <Select
                    label={t("sync.idInvalidCleanupAction")}
                    selectedKeys={[cleanupAction]}
                    onSelectionChange={(keys) => {
                      const value = Array.from(keys)[0];
                      if (
                        value === "delete" ||
                        value === "archive" ||
                        value === "to_wanted"
                      ) {
                        setCleanupAction(value);
                      }
                    }}
                  >
                    <SelectItem key="archive">
                      {t("sync.idInvalidActionToRawDelete")}
                    </SelectItem>
                    <SelectItem key="to_wanted">
                      {t("sync.migrateToWanted")}
                    </SelectItem>
                    <SelectItem key="delete">
                      {t("sync.idInvalidActionDelete")}
                    </SelectItem>
                  </Select>
                </HoverHint>
                <HoverHint content={t("sync.idInvalidCleanupDelayHint")}>
                  <Input
                    type="number"
                    label={t("sync.idInvalidCleanupDelay")}
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
                </HoverHint>
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
