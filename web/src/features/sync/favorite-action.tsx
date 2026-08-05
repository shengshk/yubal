import { Button } from "@heroui/react";
import { HeartIcon, ThumbsUpIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

type Props = {
  /** remote = YTM ID is valid; local = tags have passed local verification. */
  kind: "remote" | "local";
  active: boolean;
  busy?: boolean;
  disabled?: boolean;
  className: string;
  onPress?: () => void;
};

/**
 * The one and only collection action shown beside a track.
 *
 * A valid YTM ID uses the remote thumbs-up state.  A verified local-only
 * track uses the local-heart state.  Callers decide eligibility; this
 * component intentionally owns the visual and wording so every list agrees.
 */
export function FavoriteAction({
  kind,
  active,
  busy = false,
  disabled = false,
  className,
  onPress,
}: Props) {
  const { t } = useTranslation();
  const remote = kind === "remote";
  const label = remote
    ? active
      ? t("sync.unlikeYtm")
      : t("sync.likeYtm")
    : active
      ? t("sync.removeLocalHeart")
      : t("sync.addLocalHeart");

  return (
    <Button
      variant="light"
      size="sm"
      isIconOnly
      isLoading={busy}
      isDisabled={busy || disabled}
      className={`${className} ${
        disabled
          ? "opacity-40"
          : active
          ? "text-danger hover:text-danger"
          : remote
            ? "hover:text-primary"
            : "hover:text-danger"
      }`}
      aria-label={label}
      title={label}
      onPress={() => onPress?.()}
    >
      {remote ? (
        <ThumbsUpIcon className="h-3.5 w-3.5" fill={active ? "currentColor" : "none"} />
      ) : (
        <HeartIcon className="h-3.5 w-3.5" fill={active ? "currentColor" : "none"} />
      )}
    </Button>
  );
}
