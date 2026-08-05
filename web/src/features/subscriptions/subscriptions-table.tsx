import type { Subscription } from "@/api/subscriptions";
import { ConfirmationModal } from "@/components/common/confirmation-modal";
import { EmptyState } from "@/components/common/empty-state";
import { useTimeAgo } from "@/hooks/use-time-ago";
import { Button, Image, Switch } from "@heroui/react";
import {
  CheckIcon,
  InboxIcon,
  ListMusicIcon,
  PencilIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

function TimeAgo({ dateString }: { dateString: string | null | undefined }) {
  const timeAgo = useTimeAgo(dateString);
  return (
    <span className="text-foreground-500 font-mono text-sm">{timeAgo}</span>
  );
}

type SaveFolderHandler = (
  id: string,
  saveFolder: string,
  confirm?: boolean,
) => Promise<"ok" | "folder_conflict" | "error">;

type SubscriptionsTableProps = {
  subscriptions: Subscription[];
  isLoading?: boolean;
  isSchedulerEnabled?: boolean;
  onToggleEnabled: (id: string, enabled: boolean) => void;
  onSaveFolder: SaveFolderHandler;
  onSync: (id: string) => void;
  onDelete: (id: string) => void;
};

function FolderEditor({
  value,
  disabled,
  onChange,
  onSubmit,
  onCancel,
}: {
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  return (
    <input
      ref={inputRef}
      value={value}
      disabled={disabled}
      aria-label={t("playlists.columns.saveFolder")}
      className="text-foreground border-primary w-full min-w-0 border-0 border-b bg-transparent py-0.5 font-mono text-sm outline-none"
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onSubmit();
        }
        if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
    />
  );
}

export function SubscriptionsTable({
  subscriptions,
  isLoading,
  isSchedulerEnabled,
  onToggleEnabled,
  onSaveFolder,
  onSync,
  onDelete,
}: SubscriptionsTableProps) {
  const { t } = useTranslation();
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [folderConflict, setFolderConflict] = useState<Subscription | null>(
    null,
  );

  const cancelEdit = () => {
    setEditingId(null);
    setDraft("");
    setSaving(false);
  };

  const startEdit = (subscription: Subscription) => {
    setEditingId(subscription.id);
    setDraft(subscription.save_folder);
  };

  const saveEdit = async (subscription: Subscription) => {
    const trimmed = draft.trim();
    if (!trimmed) return;
    if (trimmed === subscription.save_folder) {
      cancelEdit();
      return;
    }

    setSaving(true);
    let result = await onSaveFolder(subscription.id, trimmed);
    if (result === "folder_conflict") {
      setSaving(false);
      setFolderConflict(subscription);
      return;
    }
    setSaving(false);
    if (result === "ok") {
      cancelEdit();
    }
  };

  if (!isLoading && subscriptions.length === 0) {
    return <EmptyState icon={InboxIcon} title={t("playlists.empty")} />;
  }

  return (
    <>
      <div className="border-default-200 overflow-x-auto rounded-xl border">
        <table className="w-full min-w-[720px] border-collapse text-left">
          <thead>
            <tr className="border-default-200 border-b">
              <th className="text-foreground-500 px-4 py-3 text-xs font-semibold tracking-wide uppercase">
                {t("playlists.columns.playlist")}
              </th>
              <th className="text-foreground-500 px-4 py-3 text-xs font-semibold tracking-wide uppercase">
                {t("playlists.columns.saveFolder")}
              </th>
              <th className="text-foreground-500 px-4 py-3 text-xs font-semibold tracking-wide uppercase">
                {t("playlists.columns.lastSynced")}
              </th>
              <th className="text-foreground-500 px-4 py-3 text-xs font-semibold tracking-wide uppercase">
                {t("playlists.columns.limit")}
              </th>
              <th className="text-foreground-500 px-4 py-3 text-center text-xs font-semibold tracking-wide uppercase">
                {t("playlists.columns.enabled")}
              </th>
              <th className="text-foreground-500 px-4 py-3 text-center text-xs font-semibold tracking-wide uppercase">
                {t("playlists.columns.actions")}
              </th>
            </tr>
          </thead>
          <tbody>
            {isLoading ? (
              <tr>
                <td
                  colSpan={6}
                  className="text-foreground-400 px-4 py-8 text-center font-mono text-sm"
                >
                  {t("common.loading")}
                </td>
              </tr>
            ) : (
              subscriptions.map((subscription) => {
                const isEditing = editingId === subscription.id;
                return (
                  <tr
                    key={subscription.id}
                    className="border-default-100 border-b last:border-b-0"
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-4 max-md:gap-0">
                        {subscription.thumbnail_url ? (
                          <Image
                            alt={t("playlists.thumbnailAlt")}
                            src={subscription.thumbnail_url}
                            width={40}
                            height={40}
                            radius="md"
                            fallbackSrc=""
                            className="max-md:hidden"
                          />
                        ) : (
                          <div className="bg-content3 flex h-8 w-8 shrink-0 items-center justify-center rounded">
                            <ListMusicIcon
                              width={40}
                              height={40}
                              className="text-foreground-400 max-md:hidden"
                            />
                          </div>
                        )}
                        <span className="font-mono text-sm">
                          {subscription.name}
                        </span>
                      </div>
                    </td>

                    <td className="px-4 py-3">
                      {isEditing ? (
                        <FolderEditor
                          value={draft}
                          disabled={saving}
                          onChange={setDraft}
                          onSubmit={() => {
                            void saveEdit(subscription);
                          }}
                          onCancel={cancelEdit}
                        />
                      ) : (
                        <span className="font-mono text-sm">
                          {subscription.save_folder}
                        </span>
                      )}
                    </td>

                    <td className="px-4 py-3">
                      <TimeAgo dateString={subscription.last_synced_at} />
                    </td>

                    <td className="px-4 py-3">
                      <span className="text-foreground-500 font-mono text-sm">
                        {subscription.max_items ?? "∞"}
                      </span>
                    </td>

                    <td className="px-4 py-3">
                      <div className="flex justify-center">
                        <Switch
                          size="sm"
                          isDisabled={!isSchedulerEnabled}
                          isSelected={subscription.enabled}
                          onValueChange={(enabled) =>
                            onToggleEnabled(subscription.id, enabled)
                          }
                          aria-label={t("playlists.toggleAutoSync")}
                        />
                      </div>
                    </td>

                    <td className="px-4 py-3">
                      <div className="flex items-center justify-center gap-1">
                        {isEditing ? (
                          <Button
                            variant="light"
                            size="sm"
                            isIconOnly
                            isDisabled={saving || !draft.trim()}
                            className="text-foreground-500 hover:text-success"
                            aria-label={t("playlists.saveFolderAction")}
                            onPress={() => {
                              void saveEdit(subscription);
                            }}
                          >
                            <CheckIcon className="h-4 w-4" />
                          </Button>
                        ) : (
                          <Button
                            variant="light"
                            size="sm"
                            isIconOnly
                            isDisabled={editingId !== null}
                            className="text-foreground-500 hover:text-primary"
                            aria-label={t("playlists.editFolderAction")}
                            onPress={() => startEdit(subscription)}
                          >
                            <PencilIcon className="h-4 w-4" />
                          </Button>
                        )}
                        <Button
                          variant="light"
                          size="sm"
                          isIconOnly
                          isDisabled={isEditing}
                          className="text-foreground-500 hover:text-primary"
                          onPress={() => onSync(subscription.id)}
                        >
                          <RefreshCwIcon className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="light"
                          size="sm"
                          isIconOnly
                          isDisabled={isEditing}
                          className="text-foreground-500 hover:text-danger"
                          onPress={() => onDelete(subscription.id)}
                        >
                          <Trash2Icon className="h-4 w-4" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <ConfirmationModal
        isOpen={folderConflict !== null}
        message={t("playlists.folderConflictConfirm", {
          folder: draft.trim(),
        })}
        isBusy={saving}
        onClose={() => setFolderConflict(null)}
        onConfirm={() => {
          setFolderConflict(null);
        }}
      />
    </>
  );
}
