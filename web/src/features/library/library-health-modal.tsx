import { getLibraryHealth, type LibraryHealth } from "@/api/library-health";
import {
  Button,
  Modal,
  ModalBody,
  ModalContent,
  ModalFooter,
  ModalHeader,
} from "@heroui/react";
import { AlertTriangleIcon, RefreshCwIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const POLL_INTERVAL_MS = 15000;

/**
 * Fullscreen, non-dismissable gate. Mounted once at the app root so every
 * route freezes the same way when the Download/External mounts are unsafe
 * (different filesystem, missing sentinel, etc). Backend absence (dev/CI
 * before the route ships) is treated as "unknown" and never blocks the UI.
 */
export function LibraryHealthModal() {
  const { t } = useTranslation();
  const [health, setHealth] = useState<LibraryHealth | null>(null);
  const [checking, setChecking] = useState(false);

  const refresh = useCallback(async () => {
    setChecking(true);
    const result = await getLibraryHealth();
    setChecking(false);
    if (result) setHealth(result);
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
    const onFocus = () => {
      void refresh();
    };
    window.addEventListener("focus", onFocus);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  const blocked = health !== null && health.status !== "healthy";
  const status = health?.status ?? "mount_suspect";

  return (
    <Modal
      isOpen={blocked}
      placement="center"
      backdrop="blur"
      isDismissable={false}
      isKeyboardDismissDisabled
      hideCloseButton
    >
      <ModalContent>
        <ModalHeader className="text-danger flex items-center gap-2">
          <AlertTriangleIcon className="h-5 w-5" />
          {t("libraryHealth.title")}
        </ModalHeader>
        <ModalBody className="gap-2 text-sm">
          <p className="text-foreground">{t(`libraryHealth.status.${status}`)}</p>
          {health?.reason ? (
            <p className="text-foreground-500 text-xs">{health.reason}</p>
          ) : null}
          <ul className="text-foreground-400 mt-2 flex flex-col gap-1 text-xs">
            <li>
              {t("libraryHealth.sameFilesystem")}:{" "}
              {health?.same_filesystem
                ? t("libraryHealth.yes")
                : t("libraryHealth.no")}
            </li>
            <li>
              {t("libraryHealth.downloadSentinel")}:{" "}
              {health?.download_sentinel_ok
                ? t("libraryHealth.ok")
                : t("libraryHealth.missing")}
            </li>
            <li>
              {t("libraryHealth.externalSentinel")}:{" "}
              {health?.external_sentinel_ok
                ? t("libraryHealth.ok")
                : t("libraryHealth.missing")}
            </li>
          </ul>
        </ModalBody>
        <ModalFooter>
          <Button
            color="danger"
            variant="flat"
            isLoading={checking}
            startContent={checking ? undefined : <RefreshCwIcon className="h-4 w-4" />}
            onPress={() => {
              void refresh();
            }}
          >
            {t("libraryHealth.retry")}
          </Button>
        </ModalFooter>
      </ModalContent>
    </Modal>
  );
}
