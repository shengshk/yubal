import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  deleteCookies,
  getCookiesStatus,
  uploadCookies,
  type CookiesStatus,
} from "@/api/cookies";
import { showErrorToast, showSuccessToast } from "@/lib/toast";

interface UseCookiesReturn {
  cookiesConfigured: boolean;
  cookiesStatus: CookiesStatus;
  isUploading: boolean;
  isDeleting: boolean;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  handleFileSelect: (e: React.ChangeEvent<HTMLInputElement>) => Promise<void>;
  handleDelete: () => Promise<void>;
  handleDropdownAction: (key: React.Key) => void;
  triggerFileUpload: () => void;
  refreshCookiesStatus: () => Promise<void>;
}

const EMPTY_STATUS: CookiesStatus = {
  configured: false,
  authenticated: false,
  auth_complete: false,
  expired: false,
  expiring_soon: false,
  expires_at: null,
  days_remaining: null,
  status: "missing",
  missing: [],
};

export function useCookies(): UseCookiesReturn {
  const { t } = useTranslation();
  const [cookiesStatus, setCookiesStatus] =
    useState<CookiesStatus>(EMPTY_STATUS);
  const [isUploading, setIsUploading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const refreshCookiesStatus = useCallback(async () => {
    try {
      setCookiesStatus(await getCookiesStatus());
    } catch {
      // Fail silently - cookies status is non-critical
    }
  }, []);

  useEffect(() => {
    void refreshCookiesStatus();
  }, [refreshCookiesStatus]);

  const handleFileSelect = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      setIsUploading(true);
      try {
        const content = await file.text();
        const success = await uploadCookies(content);

        if (success) {
          const next = await getCookiesStatus();
          setCookiesStatus(next);
          if (next.status === "expired" || next.status === "incomplete") {
            showErrorToast(
              t("cookies.uploadedTitle"),
              t("cookies.uploadedButInvalidDesc"),
            );
          } else if (next.status === "expiring_soon") {
            showSuccessToast(
              t("cookies.uploadedTitle"),
              t("cookies.expiringSoon", { days: next.days_remaining ?? 0 }),
            );
          } else {
            showSuccessToast(
              t("cookies.uploadedTitle"),
              t("cookies.uploadedDesc"),
            );
          }
        } else {
          showErrorToast(
            t("cookies.uploadFailedTitle"),
            t("cookies.uploadFailedDesc"),
          );
        }
      } catch {
        showErrorToast(
          t("cookies.uploadFailedTitle"),
          t("cookies.readFailedDesc"),
        );
      } finally {
        setIsUploading(false);
        if (fileInputRef.current) {
          fileInputRef.current.value = "";
        }
      }
    },
    [t],
  );

  const handleDelete = useCallback(async () => {
    setIsDeleting(true);
    try {
      const success = await deleteCookies();
      if (success) {
        setCookiesStatus(EMPTY_STATUS);
        showSuccessToast(t("cookies.deletedTitle"), t("cookies.deletedDesc"));
      } else {
        showErrorToast(
          t("cookies.deleteFailedTitle"),
          t("cookies.deleteFailedDesc"),
        );
      }
    } catch {
      showErrorToast(
        t("cookies.deleteFailedTitle"),
        t("cookies.deleteErrorDesc"),
      );
    } finally {
      setIsDeleting(false);
    }
  }, [t]);

  const triggerFileUpload = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleDropdownAction = useCallback(
    (key: React.Key) => {
      if (key === "upload") {
        triggerFileUpload();
      } else if (key === "delete") {
        handleDelete();
      }
    },
    [triggerFileUpload, handleDelete],
  );

  return {
    cookiesConfigured: cookiesStatus.configured,
    cookiesStatus,
    isUploading,
    isDeleting,
    fileInputRef,
    handleFileSelect,
    handleDelete,
    handleDropdownAction,
    triggerFileUpload,
    refreshCookiesStatus,
  };
}
