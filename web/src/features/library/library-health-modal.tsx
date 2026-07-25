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
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

const HEALTHY_POLL_INTERVAL_MS = 60000;
const UNHEALTHY_POLL_INTERVAL_MS = 10000;

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
  const inFlightRef = useRef<Promise<LibraryHealth | null> | null>(null);

  const refresh = useCallback((): Promise<LibraryHealth | null> => {
    if (inFlightRef.current) return inFlightRef.current;
    setChecking(true);
    const request = getLibraryHealth()
      .then((result) => {
        if (result) setHealth(result);
        return result;
      })
      .finally(() => {
        setChecking(false);
        inFlightRef.current = null;
      });
    inFlightRef.current = request;
    return request;
  }, []);

  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    let runToken = 0;

    const schedule = (result: LibraryHealth | null) => {
      if (stopped || document.visibilityState === "hidden") return;
      const delay =
        result?.status === "healthy"
          ? HEALTHY_POLL_INTERVAL_MS
          : UNHEALTHY_POLL_INTERVAL_MS;
      timer = window.setTimeout(() => {
        void run();
      }, delay);
    };

    const run = async () => {
      const token = ++runToken;
      if (timer !== undefined) window.clearTimeout(timer);
      timer = undefined;
      if (document.visibilityState === "hidden") return;
      const result = await refresh();
      if (token !== runToken) return;
      schedule(result);
    };

    const wake = () => {
      if (document.visibilityState === "visible") void run();
    };
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        runToken += 1;
        if (timer !== undefined) window.clearTimeout(timer);
        timer = undefined;
        return;
      }
      wake();
    };

    void run();
    window.addEventListener("focus", wake);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      stopped = true;
      runToken += 1;
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("focus", wake);
      document.removeEventListener("visibilitychange", onVisibilityChange);
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
          <p className="text-foreground">
            {t(`libraryHealth.status.${status}`)}
          </p>
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
            startContent={
              checking ? undefined : <RefreshCwIcon className="h-4 w-4" />
            }
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
