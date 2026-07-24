import { updateExternalPlaylist, type ExternalPlaylist } from "@/api/external";
import { showErrorToast, showSuccessToast } from "@/lib/toast";
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

type Props = {
  playlist: ExternalPlaylist | null;
  isOpen: boolean;
  isSchedulerEnabled?: boolean;
  onClose: () => void;
  onSaved: (playlist: ExternalPlaylist) => void;
};

export function ExternalPlaylistEditModal({
  playlist,
  isOpen,
  isSchedulerEnabled = true,
  onClose,
  onSaved,
}: Props) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useState(false);
  const [maxItems, setMaxItems] = useState(50);
  const [jitterSeconds, setJitterSeconds] = useState(600);
  const [offlineMarking, setOfflineMarking] = useState(true);
  const [offlineCleanup, setOfflineCleanup] = useState(false);
  const [cleanupAction, setCleanupAction] = useState<"delete" | "archive">(
    "archive",
  );
  const [cleanupDelayHours, setCleanupDelayHours] = useState(72);
  const [allowMutate, setAllowMutate] = useState(false);
  const [showRaw, setShowRaw] = useState(true);
  const [showJunk, setShowJunk] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!playlist || !isOpen) return;
    setEnabled(playlist.enabled);
    setMaxItems(playlist.max_items);
    setJitterSeconds(playlist.sync_jitter_seconds);
    setOfflineMarking(playlist.offline_marking_enabled);
    setOfflineCleanup(playlist.offline_cleanup_enabled ?? false);
    setCleanupAction(playlist.offline_cleanup_action ?? "archive");
    setCleanupDelayHours(playlist.offline_cleanup_delay_hours ?? 72);
    setAllowMutate(playlist.allow_mutate);
    setShowRaw(playlist.show_raw);
    setShowJunk(Boolean(playlist.show_raw && playlist.show_junk));
  }, [playlist, isOpen]);

  const handleShowRawToggle = (next: boolean) => {
    setShowRaw(next);
    if (!next) setShowJunk(false);
  };

  const handleShowJunkToggle = (next: boolean) => {
    if (next && !showRaw) return;
    setShowJunk(next);
  };

  const handleOfflineToggle = (next: boolean) => {
    if (next === offlineMarking) return;
    const ok = window.confirm(
      next
        ? t("sync.externalOfflineMarkingEnableWarn")
        : t("sync.externalOfflineMarkingDisableWarn"),
    );
    if (ok) setOfflineMarking(next);
  };

  const handleReadonlyToggle = (nextAllowMutate: boolean) => {
    if (nextAllowMutate === allowMutate) return;
    // nextAllowMutate true = writable; false = read-only
    const ok = window.confirm(
      nextAllowMutate
        ? t("sync.externalReadonlyDisableWarn")
        : t("sync.externalReadonlyEnableWarn"),
    );
    if (ok) setAllowMutate(nextAllowMutate);
  };

  const isSystemPlaylist =
    playlist?.dir_name === "default" || playlist?.dir_name === "delete";

  const handleSave = async () => {
    if (!playlist) return;
    setSaving(true);
    const result = await updateExternalPlaylist(playlist.dir_name, {
      enabled,
      max_items: maxItems >= 1 ? maxItems : 50,
      sync_jitter_seconds: Math.min(600, Math.max(0, jitterSeconds)),
      offline_marking_enabled: offlineMarking,
      offline_cleanup_enabled: offlineMarking && offlineCleanup,
      offline_cleanup_action: cleanupAction,
      offline_cleanup_delay_hours: Math.min(
        8760,
        Math.max(0, cleanupDelayHours),
      ),
      allow_mutate: isSystemPlaylist ? true : allowMutate,
      show_raw: showRaw,
      show_junk: showRaw ? showJunk : false,
    });
    setSaving(false);
    if ("error" in result) {
      showErrorToast(t("sync.externalSettingsSaveFailed"), result.error);
      return;
    }
    onSaved(result);
    showSuccessToast(t("settings.savedTitle"), t("settings.savedDesc"));
    onClose();
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
        <ModalHeader>
          {t("sync.editExternalTitle", { name: playlist?.dir_name ?? "" })}
        </ModalHeader>
        <ModalBody className="gap-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.externalAutoRecover")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.externalAutoRecoverHint")}
              </p>
            </div>
            <Switch
              isSelected={enabled}
              isDisabled={!isSchedulerEnabled}
              onValueChange={setEnabled}
              aria-label={t("sync.externalAutoRecover")}
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
                {t("sync.externalOfflineMarking")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.externalOfflineMarkingHint")}
              </p>
            </div>
            <Switch
              isSelected={offlineMarking}
              onValueChange={handleOfflineToggle}
              aria-label={t("sync.externalOfflineMarking")}
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

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.externalShowRaw")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.externalShowRawHint")}
              </p>
            </div>
            <Switch
              isSelected={showRaw}
              onValueChange={handleShowRawToggle}
              aria-label={t("sync.externalShowRaw")}
            />
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.externalShowJunk")}
              </p>
              <p className="text-foreground-400 text-xs">
                {t("sync.externalShowJunkHint")}
              </p>
            </div>
            <Switch
              isSelected={showJunk}
              isDisabled={!showRaw}
              onValueChange={handleShowJunkToggle}
              aria-label={t("sync.externalShowJunk")}
            />
          </div>

          <div className="flex items-center justify-between gap-3">
            <div>
              <p className="text-sm font-medium">
                {t("sync.externalReadonly")}
              </p>
              <p className="text-foreground-400 text-xs">
                {isSystemPlaylist
                  ? t("sync.externalReadonlySystemLocked")
                  : t("sync.externalReadonlyHint")}
              </p>
            </div>
            <Switch
              isSelected={!allowMutate}
              isDisabled={isSystemPlaylist}
              onValueChange={(readonly) => handleReadonlyToggle(!readonly)}
              aria-label={t("sync.externalReadonly")}
            />
          </div>
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
