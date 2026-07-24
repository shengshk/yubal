import type { ExternalDeleteMode } from "@/api/external";
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
  dirName: string;
  /** Writable playlists may touch raw/matched files; ID-invalid cleanup always allowed. */
  allowMutate: boolean;
  onClose: () => void;
  onConfirm: (mode: ExternalDeleteMode) => Promise<boolean>;
};

/** Clear organized links — Raw stays. Unmatched index alone is never cleared
 * (that would leave zombie raw files); use delete_unmatched when writable. */
const LEDGER_MODES: ExternalDeleteMode[] = ["forget_matched"];

/** ID失效：删文件+列表，或列表清掉、文件进 Raw/Delete。只读也可。 */
const ID_INVALID_MODES: ExternalDeleteMode[] = [
  "clear_offline_delete",
  "clear_offline_to_raw_delete",
];

/** Other file ops — writable only. */
const FILE_MODES: ExternalDeleteMode[] = [
  "delete_matched",
  "move_matched_to_direct",
  "delete_unmatched",
  "delete_all",
];

const DANGER: ReadonlySet<ExternalDeleteMode> = new Set([
  "delete_matched",
  "delete_unmatched",
  "delete_all",
  "clear_offline_delete",
]);

export function ExternalPlaylistDeleteModal({
  isOpen,
  dirName,
  allowMutate,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<"choose" | "confirm">("choose");
  const [mode, setMode] = useState<ExternalDeleteMode>("forget_matched");

  const reset = () => {
    setStep("choose");
    setMode("forget_matched");
    setBusy(false);
  };

  const handleClose = () => {
    if (busy) return;
    reset();
    onClose();
  };

  const titleKey = (m: ExternalDeleteMode): string =>
    ({
      forget_matched: "sync.forgetExternalMatched",
      delete_matched: "sync.deleteExternalMatched",
      move_matched_to_direct: "sync.deleteExternalMoveMatched",
      add_matched_to_direct: "sync.deleteExternalMoveMatched",
      delete_unmatched: "sync.deleteExternalUnmatched",
      delete_all: "sync.deleteExternalAll",
      clear_offline_delete: "sync.clearIdInvalidDelete",
      clear_offline_to_raw_delete: "sync.clearIdInvalidToRawDelete",
    })[m];

  const hintKey = (m: ExternalDeleteMode): string =>
    ({
      forget_matched: "sync.forgetExternalMatchedHint",
      delete_matched: "sync.deleteExternalMatchedHint",
      move_matched_to_direct: "sync.deleteExternalMoveMatchedHint",
      add_matched_to_direct: "sync.deleteExternalMoveMatchedHint",
      delete_unmatched: "sync.deleteExternalUnmatchedHint",
      delete_all: "sync.deleteExternalAllHint",
      clear_offline_delete: "sync.clearIdInvalidDeleteHint",
      clear_offline_to_raw_delete: "sync.clearIdInvalidToRawDeleteHint",
    })[m];

  const confirmKey = (m: ExternalDeleteMode): string =>
    ({
      forget_matched: "sync.forgetExternalConfirmMatched",
      delete_matched: "sync.deleteExternalConfirmMatched",
      move_matched_to_direct: "sync.deleteExternalConfirmMove",
      add_matched_to_direct: "sync.deleteExternalConfirmMove",
      delete_unmatched: "sync.deleteExternalConfirmUnmatched",
      delete_all: "sync.deleteExternalConfirmAll",
      clear_offline_delete: "sync.clearIdInvalidConfirmDelete",
      clear_offline_to_raw_delete: "sync.clearIdInvalidConfirmRawDelete",
    })[m];

  const isLedger = LEDGER_MODES.includes(mode);
  const isSoftConfirm =
    isLedger || mode === "clear_offline_to_raw_delete" || mode === "move_matched_to_direct";

  const renderModeButton = (m: ExternalDeleteMode, enabled: boolean) => (
    <Button
      key={m}
      variant={mode === m ? "solid" : "flat"}
      color={
        mode === m ? (DANGER.has(m) ? "danger" : "primary") : "default"
      }
      className="justify-start h-auto py-3 whitespace-normal"
      isDisabled={!enabled}
      onPress={() => {
        if (enabled) setMode(m);
      }}
    >
      <span className="text-left">
        <span className="block font-medium">{t(titleKey(m))}</span>
        <span className="text-foreground-400 text-xs">{t(hintKey(m))}</span>
      </span>
    </Button>
  );

  return (
    <Modal isOpen={isOpen} onClose={handleClose} placement="center" size="lg">
      <ModalContent>
        <ModalHeader>{t("sync.deleteExternalTitle")}</ModalHeader>
        <ModalBody className="gap-3 text-sm">
          {step === "choose" ? (
            <>
              <p>{t("sync.deleteExternalBody", { name: dirName })}</p>
              <p className="text-foreground-400 text-xs">
                {t("sync.deleteExternalHint")}
              </p>

              <div className="flex flex-col gap-2">
                <p className="text-xs font-medium text-foreground-500">
                  {t("sync.deleteExternalSectionLedger")}
                </p>
                <p className="text-foreground-400 text-xs -mt-1">
                  {t("sync.deleteExternalSectionLedgerHint")}
                </p>
                {LEDGER_MODES.map((m) => renderModeButton(m, true))}
              </div>

              <div className="flex flex-col gap-2 mt-2">
                <p className="text-xs font-medium text-foreground-500">
                  {t("sync.deleteSectionIdInvalid")}
                </p>
                <p className="text-foreground-400 text-xs -mt-1">
                  {t("sync.deleteSectionIdInvalidHint")}
                </p>
                {ID_INVALID_MODES.map((m) => renderModeButton(m, true))}
              </div>

              <div className="flex flex-col gap-2 mt-2">
                <p className="text-xs font-medium text-foreground-500">
                  {t("sync.deleteExternalSectionFiles")}
                </p>
                {!allowMutate ? (
                  <p className="text-warning text-xs">
                    {t("sync.deleteExternalReadonlyBlock")}
                  </p>
                ) : (
                  <p className="text-foreground-400 text-xs -mt-1">
                    {t("sync.deleteExternalSectionFilesHint")}
                  </p>
                )}
                {FILE_MODES.map((m) => renderModeButton(m, allowMutate))}
              </div>
            </>
          ) : (
            <>
              <p>{t(confirmKey(mode), { name: dirName })}</p>
              <p
                className={
                  isSoftConfirm
                    ? "text-foreground-400 text-xs"
                    : "text-danger text-xs font-medium"
                }
              >
                {isLedger
                  ? t("sync.forgetExternalWarn")
                  : mode.startsWith("clear_offline")
                    ? t("sync.clearStaleWarn")
                    : t("sync.deleteExternalWarn")}
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
              color={isSoftConfirm ? "primary" : "danger"}
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
              {isLedger || mode === "clear_offline_to_raw_delete"
                ? t("sync.continue")
                : mode === "move_matched_to_direct"
                  ? t("sync.continue")
                  : t("sync.deleteFilesForever")}
            </Button>
          )}
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
