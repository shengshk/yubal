import type { ExternalDeleteMode } from "@/api/external";
import { externalPlaylistDisplayName } from "@/lib/playlist-labels";
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
  /** Tag-validation failures whose immutable original source permits cleanup. */
  metaRejectedMutableCount: number;
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
];
const REJECTED_MODES: ExternalDeleteMode[] = [
  "archive_meta_rejected",
  "delete_meta_rejected",
];
const PRIMARY_MODES: ExternalDeleteMode[] = ["forget_matched", "delete_all"];

const DANGER: ReadonlySet<ExternalDeleteMode> = new Set([
  "delete_matched",
  "delete_unmatched",
  "delete_meta_rejected",
  "delete_all",
  "clear_offline_delete",
]);

export function ExternalPlaylistDeleteModal({
  isOpen,
  dirName,
  allowMutate,
  metaRejectedMutableCount,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const displayName = externalPlaylistDisplayName(dirName, t);
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
      add_meta_verified_to_wanted: "sync.addMetaVerifiedToWanted",
      delete_unmatched: "sync.deleteExternalUnmatched",
      archive_meta_rejected: "sync.archiveMetaRejected",
      delete_meta_rejected: "sync.deleteMetaRejected",
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
      add_meta_verified_to_wanted: "sync.addMetaVerifiedToWantedHint",
      delete_unmatched: "sync.deleteExternalUnmatchedHint",
      archive_meta_rejected: "sync.archiveMetaRejectedHint",
      delete_meta_rejected: "sync.deleteMetaRejectedHint",
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
      add_meta_verified_to_wanted: "sync.addMetaVerifiedToWantedHint",
      delete_unmatched: "sync.deleteExternalConfirmUnmatched",
      archive_meta_rejected: "sync.archiveMetaRejectedConfirm",
      delete_meta_rejected: "sync.deleteMetaRejectedConfirm",
      delete_all: "sync.deleteExternalConfirmAll",
      clear_offline_delete: "sync.clearIdInvalidConfirmDelete",
      clear_offline_to_raw_delete: "sync.clearIdInvalidConfirmRawDelete",
    })[m];

  const isLedger = LEDGER_MODES.includes(mode);
  const isSoftConfirm =
    isLedger ||
    mode === "clear_offline_to_raw_delete" ||
    mode === "move_matched_to_direct" ||
    mode === "archive_meta_rejected";

  const renderModeButton = (m: ExternalDeleteMode, enabled: boolean) => (
    <Button
      key={m}
      variant={mode === m ? "solid" : "flat"}
      color={mode === m ? (DANGER.has(m) ? "danger" : "primary") : "default"}
      className="h-auto justify-start py-3 whitespace-normal"
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
              <p>{t("sync.deleteExternalBody", { name: displayName })}</p>
              <p className="text-foreground-400 text-xs">
                {t("sync.deleteExternalHint")}
              </p>

              <div className="flex flex-col gap-2">
                <p className="text-foreground-500 text-xs font-medium">
                  {t("sync.deletePrimaryTitle")}
                </p>
                <p className="text-foreground-400 -mt-1 text-xs">
                  {t("sync.deletePrimaryHint")}
                </p>
                {PRIMARY_MODES.map((m) =>
                  renderModeButton(m, m !== "delete_all" || allowMutate),
                )}
              </div>

              <details className="border-default-200 rounded-lg border">
                <summary className="hover:bg-default-100 cursor-pointer rounded-lg px-3 py-2.5 text-sm font-medium">
                  {t("sync.moreProcessing")}
                </summary>
                <div className="flex flex-col gap-4 px-3 pb-3">
                  <p className="text-foreground-400 text-xs">
                    {t("sync.moreProcessingHint")}
                  </p>
                  <div className="flex flex-col gap-2">
                    <p className="text-foreground-500 text-xs font-medium">
                      {t("sync.deleteSectionIdInvalid")}
                    </p>
                    {ID_INVALID_MODES.filter(
                      (m) =>
                        m !== "clear_offline_to_raw_delete" ||
                        dirName !== "delete",
                    ).map((m) => renderModeButton(m, true))}
                  </div>
                  {metaRejectedMutableCount > 0 ? (
                    <div className="flex flex-col gap-2">
                      <p className="text-foreground-500 text-xs font-medium">
                        {t("sync.deleteSectionMetaRejected", {
                          count: metaRejectedMutableCount,
                        })}
                      </p>
                      {REJECTED_MODES.filter(
                        (m) =>
                          m !== "archive_meta_rejected" ||
                          dirName !== "default",
                      ).map((m) => renderModeButton(m, true))}
                    </div>
                  ) : null}
                  <div className="flex flex-col gap-2">
                    <p className="text-foreground-500 text-xs font-medium">
                      {t("sync.deleteExternalSectionFiles")}
                    </p>
                    {!allowMutate ? (
                      <p className="text-warning text-xs">
                        {t("sync.deleteExternalReadonlyBlock")}
                      </p>
                    ) : null}
                    {FILE_MODES.map((m) => renderModeButton(m, allowMutate))}
                  </div>
                </div>
              </details>
            </>
          ) : (
            <>
              <p>{t(confirmKey(mode), { name: displayName })}</p>
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
                    : mode === "archive_meta_rejected"
                      ? t("sync.archiveMetaRejectedWarn")
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
              {isLedger ||
              mode === "clear_offline_to_raw_delete" ||
              mode === "archive_meta_rejected"
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
