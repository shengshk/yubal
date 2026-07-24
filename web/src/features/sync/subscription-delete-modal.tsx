import type { Subscription } from "@/api/subscriptions";
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

export type DeleteFileAction =
  | "keep_list"
  | "move_to_direct"
  | "delete"
  | "clear_offline_delete"
  | "clear_offline_to_raw_delete";

type Props = {
  subscription: Subscription | null;
  isOpen: boolean;
  externalEnabled?: boolean;
  onClose: () => void;
  onConfirm: (action: DeleteFileAction) => Promise<boolean>;
};

const LIGHT: DeleteFileAction[] = ["keep_list"];
const NOT_IN_PLAYLIST: DeleteFileAction[] = [
  "clear_offline_delete",
  "clear_offline_to_raw_delete",
];
const HEAVY: DeleteFileAction[] = ["move_to_direct", "delete"];

const DANGER: ReadonlySet<DeleteFileAction> = new Set([
  "delete",
  "clear_offline_delete",
]);

export function SubscriptionDeleteModal({
  subscription,
  isOpen,
  externalEnabled = false,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<"choose" | "confirm">("choose");
  const [mode, setMode] = useState<DeleteFileAction>("keep_list");

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

  const name = subscription?.name ?? "";

  const titleKey = (m: DeleteFileAction): string =>
    ({
      keep_list: "sync.deleteSubKeepList",
      move_to_direct: "sync.deleteSubMoveToDirect",
      delete: "sync.deleteSubWipeList",
      clear_offline_delete: "sync.clearNotInPlaylistDelete",
      clear_offline_to_raw_delete: "sync.clearNotInPlaylistToRawDelete",
    })[m];

  const hintKey = (m: DeleteFileAction): string =>
    ({
      keep_list: "sync.deleteSubKeepListHint",
      move_to_direct: "sync.deleteSubMoveToDirectHint",
      delete: "sync.deleteSubWipeListHint",
      clear_offline_delete: "sync.clearNotInPlaylistDeleteHint",
      clear_offline_to_raw_delete: "sync.clearNotInPlaylistToRawDeleteHint",
    })[m];

  const confirmText = (() => {
    switch (mode) {
      case "keep_list":
        return t("sync.deleteSubConfirmKeep", { name });
      case "move_to_direct":
        return t("sync.deleteSubConfirmMove", { name });
      case "clear_offline_delete":
        return t("sync.clearNotInPlaylistConfirmDelete");
      case "clear_offline_to_raw_delete":
        return t("sync.clearNotInPlaylistConfirmRawDelete");
      default:
        return t("sync.deleteSubConfirmWipe", { name });
    }
  })();

  const warnText = (() => {
    switch (mode) {
      case "keep_list":
        return t("sync.deleteSubConfirmKeepWarn");
      case "move_to_direct":
        return t("sync.deleteSubConfirmMoveWarn");
      case "clear_offline_delete":
      case "clear_offline_to_raw_delete":
        return t("sync.clearStaleWarn");
      default:
        return t("sync.deleteSubConfirmWipeWarn");
    }
  })();

  const renderModeButton = (m: DeleteFileAction) => {
    if (m === "clear_offline_to_raw_delete" && !externalEnabled) return null;
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
    mode === "move_to_direct" ||
    mode === "clear_offline_to_raw_delete";

  return (
    <Modal isOpen={isOpen} onClose={handleClose} placement="center" size="lg">
      <ModalContent>
        <ModalHeader>{t("sync.deleteSubscriptionTitle")}</ModalHeader>
        <ModalBody className="gap-3 text-sm">
          {step === "choose" ? (
            <>
              <p>{t("sync.deleteSubscriptionBody", { name })}</p>
              <p className="text-foreground-400 text-xs">
                {t("sync.deleteSubscriptionHint")}
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
                  {t("sync.deleteSectionNotInPlaylist")}
                </p>
                <p className="text-foreground-400 text-xs -mt-1">
                  {t("sync.deleteSectionNotInPlaylistHint")}
                </p>
                {NOT_IN_PLAYLIST.map(renderModeButton)}
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
                : mode === "move_to_direct"
                  ? t("sync.deleteSubConfirmMoveAction")
                  : mode === "clear_offline_to_raw_delete"
                    ? t("sync.continue")
                    : t("sync.deleteFilesForever")}
            </Button>
          )}
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
