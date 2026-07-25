import type { DirectDeleteMode } from "@/api/sync-ledger";
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

export type TrackDeleteMode =
  | DirectDeleteMode
  | "block"
  | "keep_match"
  | "clear_match"
  | "delete_raw"
  | "migrate_to_external"
  | "migrate_to_wanted"
  | "add_to_direct";

type Props = {
  trackLabel: string | null;
  isOpen: boolean;
  busy?: boolean;
  /**
   * direct: keep_list | block | wipe_list | migrate_to_external? | migrate_to_wanted?
   * subscription: keep_list | block | migrate_to_wanted?
   * external: keep_match | clear_match | move_to_direct?
   * external_raw: single delete_raw confirm
   */
  variant?: "direct" | "subscription" | "external" | "external_raw";
  /** Show migrate / add-to-direct options when external library is enabled. */
  externalEnabled?: boolean;
  /** Show migrate-to-wanted when wishlist is enabled (offline rows). */
  wantedEnabled?: boolean;
  /** Offline / ID-invalid / not-in-playlist row. */
  offline?: boolean;
  /**
   * Wanted migration is for dead IDs only — not “not in cloud playlist”.
   * Direct offline rows are always ID-invalid; subscriptions pass id_invalid.
   */
  allowMigrateToWanted?: boolean;
  onClose: () => void;
  onConfirm: (mode: TrackDeleteMode) => void;
};

export function TrackDeleteModal({
  trackLabel,
  isOpen,
  busy = false,
  variant = "direct",
  externalEnabled = false,
  wantedEnabled = false,
  offline = false,
  allowMigrateToWanted = false,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<TrackDeleteMode>("keep_list");

  useEffect(() => {
    if (!isOpen) return;
    if (variant === "external") setMode("keep_match");
    else if (variant === "external_raw") setMode("delete_raw");
    else setMode("keep_list");
  }, [isOpen, variant]);

  const isSub = variant === "subscription";
  const isDirect = variant === "direct";
  const isExternalMatched = variant === "external";
  const isExternalRaw = variant === "external_raw";

  const confirmLabel = (() => {
    if (isExternalRaw) return t("sync.deleteFilesForever");
    if (mode === "keep_list" || mode === "keep_match")
      return t("sync.deleteFilesKeepList");
    if (mode === "block") return t("sync.deleteTrackBlockAction");
    if (mode === "migrate_to_external") return t("sync.migrateToExternalAction");
    if (mode === "migrate_to_wanted") return t("sync.migrateToWantedAction");
    if (mode === "add_to_direct") return t("sync.addToDirectAction");
    return t("sync.deleteFilesForever");
  })();

  return (
    <Modal isOpen={isOpen} onClose={onClose} placement="center">
      <ModalContent>
        <ModalHeader>{t("sync.deleteTrackTitle")}</ModalHeader>
        <ModalBody className="gap-3 text-sm">
          <p>{t("sync.deleteTrackBody", { name: trackLabel ?? "" })}</p>
          {isExternalRaw ? (
            <p className="text-foreground-400 text-xs">
              {t("sync.deleteExternalRawHint")}
            </p>
          ) : (
            <>
              <p className="text-foreground-400 text-xs">
                {isSub
                  ? t("sync.deleteTrackHintSub")
                  : isExternalMatched
                    ? t("sync.deleteExternalHint")
                    : t("sync.deleteTrackHint")}
              </p>
              <div className="flex flex-col gap-2">
                <Button
                  variant={
                    mode === "keep_list" || mode === "keep_match"
                      ? "solid"
                      : "flat"
                  }
                  color={
                    mode === "keep_list" || mode === "keep_match"
                      ? "primary"
                      : "default"
                  }
                  className="justify-start h-auto py-3 whitespace-normal"
                  onPress={() =>
                    setMode(isExternalMatched ? "keep_match" : "keep_list")
                  }
                >
                  <span className="text-left">
                    <span className="block font-medium">
                      {isExternalMatched
                        ? t("sync.deleteExternalKeepMatch")
                        : t("sync.deleteDirectKeepList")}
                    </span>
                    <span className="text-foreground-400 text-xs">
                      {isSub
                        ? t("sync.deleteTrackKeepListHintSub")
                        : isExternalMatched
                          ? t("sync.deleteExternalKeepMatchHint")
                          : t("sync.deleteTrackKeepListHint")}
                    </span>
                  </span>
                </Button>
                {(isSub || isDirect) && (
                  <Button
                    variant={mode === "block" ? "solid" : "flat"}
                    color={mode === "block" ? "danger" : "default"}
                    className="justify-start h-auto py-3 whitespace-normal"
                    onPress={() => setMode("block")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.deleteTrackBlock")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {isDirect
                          ? t("sync.deleteTrackBlockHintDirect")
                          : t("sync.deleteTrackBlockHint")}
                      </span>
                    </span>
                  </Button>
                )}
                {isDirect && (
                  <Button
                    variant={mode === "wipe_list" ? "solid" : "flat"}
                    color={mode === "wipe_list" ? "danger" : "default"}
                    className="justify-start h-auto py-3 whitespace-normal"
                    onPress={() => setMode("wipe_list")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.deleteDirectWipeList")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {t("sync.deleteTrackWipeListHint")}
                      </span>
                    </span>
                  </Button>
                )}
                {isDirect && externalEnabled && (
                  <Button
                    variant={mode === "migrate_to_external" ? "solid" : "flat"}
                    color={
                      mode === "migrate_to_external" ? "primary" : "default"
                    }
                    className="justify-start h-auto py-3 whitespace-normal"
                    onPress={() => setMode("migrate_to_external")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.migrateToExternal")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {t("sync.migrateToExternalHint")}
                      </span>
                    </span>
                  </Button>
                )}
                {(isDirect || isSub) &&
                  wantedEnabled &&
                  offline &&
                  allowMigrateToWanted && (
                  <Button
                    variant={mode === "migrate_to_wanted" ? "solid" : "flat"}
                    color={
                      mode === "migrate_to_wanted" ? "primary" : "default"
                    }
                    className="justify-start h-auto py-3 whitespace-normal"
                    onPress={() => setMode("migrate_to_wanted")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.migrateToWanted")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {t("sync.migrateToWantedHint")}
                      </span>
                    </span>
                  </Button>
                )}
                {isExternalMatched && (
                  <Button
                    variant={
                      mode === "clear_match" || mode === "wipe_list"
                        ? "solid"
                        : "flat"
                    }
                    color={
                      mode === "clear_match" || mode === "wipe_list"
                        ? "danger"
                        : "default"
                    }
                    className="justify-start h-auto py-3 whitespace-normal"
                    onPress={() => setMode("clear_match")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.deleteExternalClearMatch")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {t("sync.deleteExternalClearMatchHint")}
                      </span>
                    </span>
                  </Button>
                )}
                {isExternalMatched && (
                  <Button
                    variant={mode === "add_to_direct" ? "solid" : "flat"}
                    color={mode === "add_to_direct" ? "primary" : "default"}
                    className="justify-start h-auto py-3 whitespace-normal"
                    onPress={() => setMode("add_to_direct")}
                  >
                    <span className="text-left">
                      <span className="block font-medium">
                        {t("sync.addToDirect")}
                      </span>
                      <span className="text-foreground-400 text-xs">
                        {t("sync.addToDirectHint")}
                      </span>
                    </span>
                  </Button>
                )}
              </div>
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button variant="light" onPress={onClose} isDisabled={busy}>
            {t("sync.cancel")}
          </Button>
          <Button
            color="danger"
            isLoading={busy}
            onPress={() => onConfirm(mode)}
          >
            {confirmLabel}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
