import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import { useTranslation } from "react-i18next";

type Props = {
  isOpen: boolean;
  title?: string;
  message: string;
  confirmLabel?: string;
  confirmColor?: "primary" | "warning" | "danger";
  isBusy?: boolean;
  onClose: () => void;
  onConfirm: () => void;
};

/** Project-styled replacement for browser confirm dialogs. */
export function ConfirmationModal({
  isOpen,
  title,
  message,
  confirmLabel,
  confirmColor = "primary",
  isBusy = false,
  onClose,
  onConfirm,
}: Props) {
  const { t } = useTranslation();
  return (
    <Modal
      isOpen={isOpen}
      onOpenChange={(open) => {
        if (!open && !isBusy) onClose();
      }}
      placement="center"
    >
      <ModalContent>
        {(close) => (
          <>
            <ModalHeader>{title ?? t("common.confirmTitle")}</ModalHeader>
            <ModalBody className="text-sm">
              <p>{message}</p>
            </ModalBody>
            <ModalFooter>
              <Button variant="light" isDisabled={isBusy} onPress={close}>
                {t("sync.cancel")}
              </Button>
              <Button
                color={confirmColor}
                isLoading={isBusy}
                onPress={onConfirm}
              >
                {confirmLabel ?? t("common.confirm")}
              </Button>
            </ModalFooter>
          </>
        )}
      </ModalContent>
    </Modal>
  );
}
