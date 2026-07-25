import type { DirectPlaylistDeleteMode } from "@/api/sync-ledger";
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

type Props = {
  isOpen: boolean;
  folder: string;
  externalEnabled?: boolean;
  onClose: () => void;
  onConfirm: (mode: DirectPlaylistDeleteMode) => Promise<boolean>;
};

const LIGHT: DirectPlaylistDeleteMode[] = ["keep_list"];
const ID_INVALID: DirectPlaylistDeleteMode[] = [
  "clear_offline_delete",
  "clear_offline_to_raw_delete",
  "clear_offline_to_wanted",
];
const HEAVY: DirectPlaylistDeleteMode[] = ["wipe_list", "migrate_to_external"];

const DANGER: ReadonlySet<DirectPlaylistDeleteMode> = new Set([
  "wipe_list",
  "clear_offline_delete",
]);

export function DirectDeleteModal({
  isOpen,
  folder,
  externalEnabled = false,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<"choose" | "confirm">("choose");
  const [mode, setMode] = useState<DirectPlaylistDeleteMode>("keep_list");

  const reset = () => {
    setStep("choose");
    setMode("keep_list");
    setBusy(false);
  };

  const handleClose = () => {
    if (busy) return;
    reset();
    onClose();
  };

  const titleKey = (m: DirectPlaylistDeleteMode): string =>
    ({
      keep_list: "sync.deleteDirectKeepList",
      wipe_list: "sync.deleteDirectWipeList",
      clear_offline_delete: "sync.clearIdInvalidDelete",
      clear_offline_to_raw_delete: "sync.clearIdInvalidToRawDelete",
      clear_offline_to_wanted: "sync.clearIdInvalidToWanted",
      migrate_to_external: "sync.migrateToExternal",
    })[m];

  const hintKey = (m: DirectPlaylistDeleteMode): string =>
    ({
      keep_list: "sync.deleteDirectKeepListHint",
      wipe_list: "sync.deleteDirectWipeListHint",
      clear_offline_delete: "sync.clearIdInvalidDeleteHint",
      clear_offline_to_raw_delete: "sync.clearIdInvalidToRawDeleteHint",
      clear_offline_to_wanted: "sync.clearIdInvalidToWantedHint",
      migrate_to_external: "sync.migrateToExternalPlaylistHint",
    })[m];

  const confirmText = (() => {
    switch (mode) {
      case "keep_list":
        return t("sync.deleteDirectConfirmKeep", { folder });
      case "wipe_list":
        return t("sync.deleteDirectConfirmWipe", { folder });
      case "clear_offline_delete":
        return t("sync.clearIdInvalidConfirmDelete");
      case "clear_offline_to_raw_delete":
        return t("sync.clearIdInvalidConfirmRawDelete");
      case "clear_offline_to_wanted":
        return t("sync.clearIdInvalidConfirmWanted");
      case "migrate_to_external":
        return t("sync.migrateDirectConfirm");
      default:
        return "";
    }
  })();

  const warnText = (() => {
    switch (mode) {
      case "keep_list":
        return t("sync.deleteDirectConfirmKeepWarn");
      case "wipe_list":
        return t("sync.deleteDirectWarn");
      case "migrate_to_external":
        return t("sync.migrateDirectWarn");
      case "clear_offline_delete":
      case "clear_offline_to_raw_delete":
      case "clear_offline_to_wanted":
        return t("sync.clearStaleWarn");
      default:
        return t("sync.clearStaleWarn");
    }
  })();

  const renderModeButton = (m: DirectPlaylistDeleteMode) => {
    if (
      (m === "clear_offline_to_raw_delete" || m === "migrate_to_external") &&
      !externalEnabled
    ) {
      return null;
    }
    return (
      <Button
        key={m}
        variant={mode === m ? "solid" : "flat"}
        color={
          mode === m ? (DANGER.has(m) ? "danger" : "primary") : "default"
        }
        className="justify-start h-auto py-3 whitespace-normal"
        onPress={() => setMode(m)}
      >
        <span className="text-left">
          <span className="block font-medium">{t(titleKey(m))}</span>
          <span className="text-foreground-400 text-xs">{t(hintKey(m))}</span>
        </span>
      </Button>
    );
  };

  const soft =
    mode === "keep_list" ||
    mode === "migrate_to_external" ||
    mode === "clear_offline_to_raw_delete" ||
    mode === "clear_offline_to_wanted";

  return (
    <Modal isOpen={isOpen} onClose={handleClose} placement="center" size="lg">
      <ModalContent>
        <ModalHeader>{t("sync.deleteDirectTitle")}</ModalHeader>
        <ModalBody className="gap-3 text-sm">
          {step === "choose" ? (
            <>
              <p>{t("sync.deleteDirectBody", { folder })}</p>
              <p className="text-foreground-400 text-xs">
                {t("sync.deleteDirectHint")}
              </p>

              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-foreground-500">
                  {t("sync.deleteSectionLight")}
                </p>
                <p className="text-foreground-400 text-xs -mt-1">
                  {t("sync.deleteSectionLightHint")}
                </p>
                {LIGHT.map(renderModeButton)}
              </div>

              <div className="flex flex-col gap-2 mt-2">
                <p className="text-xs font-medium text-foreground-500">
                  {t("sync.deleteSectionIdInvalid")}
                </p>
                <p className="text-foreground-400 text-xs -mt-1">
                  {t("sync.deleteSectionIdInvalidHint")}
                </p>
                {ID_INVALID.map(renderModeButton)}
              </div>

              <div className="flex flex-col gap-2 mt-2">
                <p className="text-xs font-medium text-foreground-500">
                  {t("sync.deleteSectionHeavy")}
                </p>
                <p className="text-foreground-400 text-xs -mt-1">
                  {t("sync.deleteSectionHeavyHint")}
                </p>
                {HEAVY.map(renderModeButton)}
              </div>
            </>
          ) : (
            <>
              <p>{confirmText}</p>
              <p
                className={
                  soft
                    ? "text-foreground-400 text-xs"
                    : "text-danger text-xs font-medium"
                }
              >
                {warnText}
              </p>
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button variant="light" onPress={handleClose} isDisabled={busy}>
            {t("sync.cancel")}
          </Button>
          {step === "choose" ? (
            <Button
              color={DANGER.has(mode) ? "danger" : "primary"}
              onPress={() => setStep("confirm")}
            >
              {t("sync.continue")}
            </Button>
          ) : (
            <Button
              color={soft ? "primary" : "danger"}
              isLoading={busy}
              onPress={() => {
                setBusy(true);
                void onConfirm(mode).then((ok) => {
                  setBusy(false);
                  if (ok) {
                    reset();
                    onClose();
                  }
                });
              }}
            >
              {mode === "keep_list"
                ? t("sync.deleteFilesKeepList")
                : mode === "migrate_to_external" ||
                    mode === "clear_offline_to_raw_delete"
                  ? t("sync.continue")
                  : t("sync.deleteFilesForever")}
            </Button>
          )}
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
