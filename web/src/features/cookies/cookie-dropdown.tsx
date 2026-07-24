import {
  Button,
  Dropdown,
  DropdownItem,
  DropdownMenu,
  DropdownTrigger,
  Link,
  Tooltip,
} from "@heroui/react";
import { CookieIcon, Trash2Icon, UploadIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

interface CookieDropdownProps {
  cookiesConfigured: boolean;
  isUploading: boolean;
  isDeleting: boolean;
  onDropdownAction: (key: React.Key) => void;
  onUploadClick: () => void;
  variant: "desktop" | "mobile";
}

export function CookieDropdown({
  cookiesConfigured,
  isUploading,
  isDeleting,
  onDropdownAction,
  onUploadClick,
  variant,
}: CookieDropdownProps) {
  const { t } = useTranslation();

  if (variant === "desktop") {
    return cookiesConfigured ? (
      <Dropdown>
        <DropdownTrigger>
          <Button
            isIconOnly
            size="sm"
            variant="light"
            aria-label={t("cookies.options")}
            isLoading={isDeleting}
          >
            <CookieIcon className="h-5 w-5 text-amber-500 dark:text-orange-300" />
          </Button>
        </DropdownTrigger>
        <CookieDropdownMenu onAction={onDropdownAction} />
      </Dropdown>
    ) : (
      <Tooltip content={t("cookies.uploadTooltip")} closeDelay={0}>
        <Button
          isIconOnly
          size="sm"
          variant="light"
          aria-label={t("cookies.upload")}
          isLoading={isUploading}
          onPress={onUploadClick}
        >
          <CookieIcon className="h-5 w-5" />
        </Button>
      </Tooltip>
    );
  }

  // Mobile variant
  return cookiesConfigured ? (
    <Dropdown>
      <DropdownTrigger>
        <Link as="button" color="foreground" className="w-full gap-2" size="lg">
          {t("cookies.configured")}
        </Link>
      </DropdownTrigger>
      <CookieDropdownMenu onAction={onDropdownAction} />
    </Dropdown>
  ) : (
    <Link
      as="button"
      color="foreground"
      className="w-full cursor-pointer gap-2"
      size="lg"
      onPress={onUploadClick}
    >
      {t("cookies.upload")}
    </Link>
  );
}

interface CookieDropdownMenuProps {
  onAction: (key: React.Key) => void;
}

function CookieDropdownMenu({ onAction }: CookieDropdownMenuProps) {
  const { t } = useTranslation();

  return (
    <DropdownMenu aria-label={t("cookies.actions")} onAction={onAction}>
      <DropdownItem
        key="upload"
        startContent={<UploadIcon className="h-4 w-4" />}
      >
        {t("cookies.uploadNew")}
      </DropdownItem>
      <DropdownItem
        key="delete"
        color="danger"
        className="text-danger"
        startContent={<Trash2Icon className="h-4 w-4" />}
      >
        {t("cookies.delete")}
      </DropdownItem>
    </DropdownMenu>
  );
}
