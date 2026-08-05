import { Tooltip } from "@heroui/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  externalPlaylistDisplayName,
  externalPlaylistPathHint,
  specialExternalPit,
} from "@/lib/playlist-labels";

type Kind =
  | "search"
  | "direct"
  | "wanted"
  | "favorite"
  | "liked"
  | "subscription"
  | "external";

type Props = {
  kind: Kind;
  /** Shown title (already localized / display name). */
  children: ReactNode;
  className?: string;
  /** Direct / subscription save folder under download/. */
  saveFolder?: string;
  /** External playlist disk dir name. */
  dirName?: string;
  /** External allow_mutate — omitted for non-external. */
  allowMutate?: boolean;
};

function TipBody({
  kindLabel,
  path,
  role,
}: {
  kindLabel: string;
  path: string;
  role: string;
}) {
  const { t } = useTranslation();
  return (
    <div className="w-[28rem] max-w-[calc(100vw-2rem)] space-y-1.5 text-left text-xs leading-relaxed">
      <p className="grid grid-cols-[auto_1fr] gap-x-1">
        <span className="text-foreground-400">{t("sync.playlistTipAttr")}</span>
        <span>{kindLabel}</span>
      </p>
      <p className="grid grid-cols-[auto_1fr] gap-x-1">
        <span className="text-foreground-400 font-sans">
          {t("sync.playlistTipPath")}
        </span>
        <span className="font-mono break-words">{path}</span>
      </p>
      <p className="grid grid-cols-[auto_1fr] gap-x-1">
        <span className="text-foreground-400">{t("sync.playlistTipRole")}</span>
        <span>{role}</span>
      </p>
    </div>
  );
}

export function PlaylistTitleTooltip({
  kind,
  children,
  className,
  saveFolder,
  dirName,
  allowMutate,
}: Props) {
  const { t } = useTranslation();

  let kindLabel: string;
  let path: string;
  let role: string;

  if (kind === "search") {
    kindLabel = t("sync.playlistTipKindSearch");
    path = t("sync.playlistTipPathSearch");
    role = t("sync.playlistTipRoleSearch");
  } else if (kind === "direct") {
    kindLabel = t("sync.playlistTipKindDirect");
    path = `download/${(saveFolder || "direct").replace(/^\/+/, "")}`;
    role = t("sync.playlistTipRoleDirect");
  } else if (kind === "liked") {
    kindLabel = t("sync.playlistTipKindLiked");
    path = "download/liked";
    role = t("sync.playlistTipRoleLiked");
  } else if (kind === "favorite") {
    kindLabel = t("sync.playlistTipKindFavorite");
    path = `download/${(saveFolder || "liked").replace(/^\/+/, "")} + wanted/`;
    role = t("sync.playlistTipRoleFavorite");
  } else if (kind === "wanted") {
    kindLabel = t("sync.playlistTipKindWanted");
    path = "wanted/";
    role = t("sync.playlistTipRoleWanted");
  } else if (kind === "subscription") {
    kindLabel = t("sync.playlistTipKindSubscription");
    path = `download/${(saveFolder || "sublist").replace(/^\/+/, "")}`;
    role = t("sync.playlistTipRoleSubscription");
  } else {
    const name = dirName || "";
    const pit = specialExternalPit(name);
    if (pit === "deleted") {
      kindLabel = t("sync.playlistTipKindDeleted");
      path = externalPlaylistPathHint(name);
      role = t("sync.playlistTipRoleDeleted");
    } else if (pit === "archive") {
      kindLabel = t("sync.playlistTipKindArchive");
      path = externalPlaylistPathHint(name);
      role = t("sync.playlistTipRoleArchive");
    } else {
      kindLabel =
        allowMutate === false
          ? t("sync.playlistTipKindExternalReadonly")
          : t("sync.playlistTipKindExternal");
      path = externalPlaylistPathHint(name);
      role = t("sync.playlistTipRoleExternal");
    }
  }

  // Keep aria label on the visible title text for screen readers.
  const ariaName =
    kind === "external" && dirName
      ? externalPlaylistDisplayName(dirName, t)
      : undefined;

  return (
    <Tooltip
      content={<TipBody kindLabel={kindLabel} path={path} role={role} />}
      placement="top-start"
      delay={400}
      closeDelay={0}
      classNames={{ content: "px-3 py-2" }}
    >
      <span className={className} aria-label={ariaName}>
        {children}
      </span>
    </Tooltip>
  );
}
