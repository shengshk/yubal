import { updateExternalPlaylist, type ExternalPlaylist } from "@/api/external";
import { HoverHint } from "@/components/common/hover-hint";
import { ConfirmationModal } from "@/components/common/confirmation-modal";
import {
  externalPlaylistDisplayName,
  specialExternalPit,
} from "@/lib/playlist-labels";
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
  const [accessMode, setAccessMode] = useState<
    "pending" | "readonly" | "managed"
  >("pending");
  const [showRaw, setShowRaw] = useState(true);
  const [showJunk, setShowJunk] = useState(true);
  const [saving, setSaving] = useState(false);
  const [pendingChange, setPendingChange] = useState<
    | { kind: "offline"; next: boolean }
    | { kind: "access"; next: "readonly" | "managed" }
    | null
  >(null);
  const cleanupActionsHint = [
    `${t("sync.idInvalidActionToRawDelete")}：${t("sync.cleanupActionArchiveHint")}`,
    `${t("sync.idInvalidActionDelete")}：${t("sync.cleanupActionDeleteHint")}`,
  ].join("\n");

  useEffect(() => {
    if (!playlist || !isOpen) return;
    setEnabled(playlist.enabled);
    setMaxItems(playlist.max_items);
    setJitterSeconds(playlist.sync_jitter_seconds);
    setOfflineMarking(playlist.offline_marking_enabled);
    setOfflineCleanup(playlist.offline_cleanup_enabled ?? false);
    setCleanupAction(playlist.offline_cleanup_action ?? "archive");
    setCleanupDelayHours(playlist.offline_cleanup_delay_hours ?? 72);
    setAccessMode(playlist.access_mode);
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
    setPendingChange({ kind: "offline", next });
  };

  const handleAccessModeChange = (next: "pending" | "readonly" | "managed") => {
    if (next === accessMode) return;
    if (next === "pending") {
      setAccessMode(next);
      return;
    }
    setPendingChange({ kind: "access", next });
  };

  const isSystemPlaylist =
    specialExternalPit(playlist?.dir_name ?? "") !== null;
  const pendingChangeMessage =
    pendingChange?.kind === "offline"
      ? pendingChange.next
        ? t("sync.externalOfflineMarkingEnableWarn")
        : t("sync.externalOfflineMarkingDisableWarn")
      : pendingChange?.next === "managed"
        ? t("sync.externalReadonlyDisableWarn")
        : t("sync.externalReadonlyEnableWarn");

  const applyPendingChange = () => {
    if (!pendingChange) return;
    if (pendingChange.kind === "offline") {
      setOfflineMarking(pendingChange.next);
    } else {
      setAccessMode(pendingChange.next);
    }
    setPendingChange(null);
  };

  const handleSave = async () => {
    if (!playlist) return;
    setSaving(true);
    const result = await updateExternalPlaylist(playlist.dir_name, {
      enabled:
        playlist.access_mode === "pending" && accessMode !== "pending"
          ? true
          : enabled,
      max_items: maxItems >= 1 ? maxItems : 50,
      sync_jitter_seconds: Math.min(600, Math.max(0, jitterSeconds)),
      offline_marking_enabled: offlineMarking,
      offline_cleanup_enabled: offlineMarking && offlineCleanup,
      offline_cleanup_action: cleanupAction,
      offline_cleanup_delay_hours: Math.min(
        8760,
        Math.max(0, cleanupDelayHours),
      ),
      access_mode: isSystemPlaylist ? "managed" : accessMode,
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
    <>
      <Modal
        isOpen={isOpen}
        onClose={() => {
          setPendingChange(null);
          onClose();
        }}
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
            {t("sync.editExternalTitle", {
              name: playlist
                ? externalPlaylistDisplayName(playlist.dir_name, t)
                : "",
            })}
          </ModalHeader>
          <ModalBody className="gap-4">
            <HoverHint content={t("sync.externalAutoRecoverHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.externalAutoRecover")}
                </p>
                <Switch
                  isSelected={enabled}
                  isDisabled={!isSchedulerEnabled}
                  onValueChange={setEnabled}
                  aria-label={t("sync.externalAutoRecover")}
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

            <HoverHint content={t("sync.externalOfflineMarkingHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.externalOfflineMarking")}
                </p>
                <Switch
                  isSelected={offlineMarking}
                  onValueChange={handleOfflineToggle}
                  aria-label={t("sync.externalOfflineMarking")}
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

            <HoverHint content={t("sync.externalShowRawHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.externalShowRaw")}
                </p>
                <Switch
                  isSelected={showRaw}
                  onValueChange={handleShowRawToggle}
                  aria-label={t("sync.externalShowRaw")}
                />
              </div>
            </HoverHint>

            <HoverHint content={t("sync.externalShowJunkHint")}>
              <div className="flex items-center justify-between gap-3">
                <p className="text-sm font-medium">
                  {t("sync.externalShowJunk")}
                </p>
                <Switch
                  isSelected={showJunk}
                  isDisabled={!showRaw}
                  onValueChange={handleShowJunkToggle}
                  aria-label={t("sync.externalShowJunk")}
                />
              </div>
            </HoverHint>

            {isSystemPlaylist ? (
              <div className="rounded-medium bg-default-100 px-3 py-2.5">
                <p className="text-sm font-medium">
                  {t("sync.externalSystemLibrary")}
                </p>
                <p className="text-foreground-500 mt-1 text-xs leading-relaxed">
                  {t("sync.externalSystemLibraryHint")}
                </p>
              </div>
            ) : (
              <HoverHint
                content={
                  playlist?.access_mode_locked
                    ? t("sync.externalAccessModeLockedHint")
                    : t("sync.externalAccessModeHint")
                }
              >
                <Select
                  label={t("sync.externalAccessMode")}
                  selectedKeys={[accessMode]}
                  isDisabled={Boolean(playlist?.access_mode_locked)}
                  onSelectionChange={(keys) => {
                    const value = Array.from(keys)[0];
                    if (
                      value === "pending" ||
                      value === "readonly" ||
                      value === "managed"
                    ) {
                      handleAccessModeChange(value);
                    }
                  }}
                >
                  {playlist?.access_mode === "pending" ? (
                    <SelectItem key="pending">
                      {t("sync.externalAccessPending")}
                    </SelectItem>
                  ) : null}
                  <SelectItem key="readonly">
                    {t("sync.externalAccessReadonly")}
                  </SelectItem>
                  <SelectItem key="managed">
                    {t("sync.externalAccessManaged")}
                  </SelectItem>
                </Select>
              </HoverHint>
            )}
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
      <ConfirmationModal
        isOpen={pendingChange !== null}
        message={pendingChangeMessage}
        confirmColor="warning"
        onClose={() => setPendingChange(null)}
        onConfirm={applyPendingChange}
      />
    </>
  );
}
