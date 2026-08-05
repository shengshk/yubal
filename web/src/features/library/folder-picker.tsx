import {
  createLibraryFolder,
  deleteLibraryFolder,
  listLibraryFolders,
  renameLibraryFolder,
  type LibraryFolders,
} from "@/api/library";
import { ConfirmationModal } from "@/components/common/confirmation-modal";
import {
  buildFolderTree,
  canCreateChild,
  canExpandFolder,
  canManageFolder,
  canSelectFolder,
  filterTreePaths,
  joinFolderPath,
  pathAncestors,
  type FolderTreeNode,
} from "@/features/library/folder-tree";
import {
  CheckIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  FolderIcon,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useTranslation } from "react-i18next";

/** Virtual tree path for the library data root (not a selectable save folder). */
export const DATA_ROOT_PATH = "";

type Props = {
  label: string;
  value: string;
  onChange: (path: string) => void;
  isDisabled?: boolean;
};

type ContextMenuState = {
  x: number;
  y: number;
  path: string;
  canCreate: boolean;
  canManage: boolean;
};

type InlineEdit =
  | { mode: "create"; parentPath: string; name: string }
  | { mode: "rename"; path: string; name: string };

function InlineNameRow({
  depth,
  name,
  busy,
  onNameChange,
  onCommit,
  onCancel,
}: {
  depth: number;
  name: string;
  busy: boolean;
  onNameChange: (name: string) => void;
  onCommit: () => void;
  onCancel: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void onCommit();
    }
    if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
    }
  };

  return (
    <div
      className="flex items-center gap-1 py-0.5 pr-2"
      style={{ paddingLeft: 4 + depth * 14 }}
    >
      <span className="inline-flex h-7 w-7 shrink-0" />
      <FolderIcon className="text-primary h-3.5 w-3.5 shrink-0 opacity-70" />
      <input
        ref={inputRef}
        disabled={busy}
        value={name}
        onChange={(event) => onNameChange(event.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => {
          if (name.trim()) void onCommit();
          else onCancel();
        }}
        className="border-primary/40 bg-content1 focus:border-primary min-w-0 flex-1 rounded border px-2 py-1 font-mono text-sm outline-none"
      />
    </div>
  );
}

function TreeRow({
  node,
  depth,
  selected,
  expanded,
  occupied,
  emptyFolders,
  inlineEdit,
  busy,
  isRoot,
  onToggle,
  onSelect,
  onContextMenu,
  onInlineNameChange,
  onInlineCommit,
  onInlineCancel,
  isDisabled,
}: {
  node: FolderTreeNode;
  depth: number;
  selected: string;
  expanded: Set<string>;
  occupied: ReadonlySet<string>;
  emptyFolders: ReadonlySet<string>;
  inlineEdit: InlineEdit | null;
  busy: boolean;
  isRoot?: boolean;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
  onContextMenu: (event: MouseEvent, path: string) => void;
  onInlineNameChange: (name: string) => void;
  onInlineCommit: () => void;
  onInlineCancel: () => void;
  isDisabled?: boolean;
}) {
  const selectable = !isRoot && canSelectFolder(node.path, occupied);
  const expandable =
    isRoot ||
    canExpandFolder(
      node.path,
      occupied,
      node.children.length > 0 ||
        (inlineEdit?.mode === "create" &&
          inlineEdit.parentPath === node.path) ||
        occupiedHasChildUnder(node.path, occupied),
    );
  const isOpen = isRoot ? true : expanded.has(node.path);
  const isSelected = !isRoot && selected === node.path;
  const renamingHere =
    inlineEdit?.mode === "rename" && inlineEdit.path === node.path;
  const createHere =
    inlineEdit?.mode === "create" &&
    inlineEdit.parentPath === (isRoot ? DATA_ROOT_PATH : node.path);

  if (renamingHere && inlineEdit) {
    return (
      <li>
        <InlineNameRow
          depth={depth}
          name={inlineEdit.name}
          busy={busy}
          onNameChange={onInlineNameChange}
          onCommit={onInlineCommit}
          onCancel={onInlineCancel}
        />
      </li>
    );
  }

  return (
    <li>
      <div
        className={`flex items-center gap-0.5 rounded-md pr-2 ${
          isSelected
            ? "bg-primary/15 text-primary"
            : selectable
              ? "hover:bg-default-100"
              : "text-foreground-500"
        }`}
        style={{ paddingLeft: 4 + depth * 14 }}
        onContextMenu={(event) => {
          if (isDisabled) return;
          onContextMenu(event, isRoot ? DATA_ROOT_PATH : node.path);
        }}
      >
        {expandable && !isRoot ? (
          <button
            type="button"
            className="text-foreground-400 hover:text-foreground flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
            aria-expanded={isOpen}
            disabled={isDisabled}
            onClick={() => onToggle(node.path)}
          >
            {isOpen ? (
              <ChevronDownIcon className="h-3.5 w-3.5" />
            ) : (
              <ChevronRightIcon className="h-3.5 w-3.5" />
            )}
          </button>
        ) : (
          <span className="text-foreground-400 inline-flex h-7 w-7 shrink-0 items-center justify-center">
            {isRoot ? <ChevronDownIcon className="h-3.5 w-3.5" /> : null}
          </span>
        )}
        <button
          type="button"
          disabled={isDisabled || !selectable}
          className={`flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-sm ${
            selectable ? "" : "cursor-default"
          } disabled:opacity-100`}
          onClick={() => {
            if (selectable) onSelect(node.path);
          }}
        >
          <FolderIcon className="h-3.5 w-3.5 shrink-0 opacity-70" />
          <span className={`truncate font-mono ${isRoot ? "font-medium" : ""}`}>
            {node.name}
          </span>
          {isSelected && (
            <CheckIcon className="text-primary ml-auto h-3.5 w-3.5 shrink-0" />
          )}
        </button>
      </div>
      {expandable && isOpen && (
        <ul className="m-0 list-none p-0">
          {node.children.map((child) => (
            <TreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              selected={selected}
              expanded={expanded}
              occupied={occupied}
              emptyFolders={emptyFolders}
              inlineEdit={inlineEdit}
              busy={busy}
              onToggle={onToggle}
              onSelect={onSelect}
              onContextMenu={onContextMenu}
              onInlineNameChange={onInlineNameChange}
              onInlineCommit={onInlineCommit}
              onInlineCancel={onInlineCancel}
              isDisabled={isDisabled}
            />
          ))}
          {createHere && inlineEdit?.mode === "create" && (
            <li>
              <InlineNameRow
                depth={depth + 1}
                name={inlineEdit.name}
                busy={busy}
                onNameChange={onInlineNameChange}
                onCommit={onInlineCommit}
                onCancel={onInlineCancel}
              />
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

function occupiedHasChildUnder(
  path: string,
  occupied: ReadonlySet<string>,
): boolean {
  const prefix = `${path}/`;
  for (const folder of occupied) {
    if (folder.startsWith(prefix)) return true;
  }
  return false;
}

export function FolderPicker({ label, value, onChange, isDisabled }: Props) {
  const { t } = useTranslation();
  const [folders, setFolders] = useState<LibraryFolders | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [inlineEdit, setInlineEdit] = useState<InlineEdit | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deletePath, setDeletePath] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const data = await listLibraryFolders();
    setFolders(data);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!value) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      for (const path of pathAncestors(value)) next.add(path);
      return next;
    });
  }, [value]);

  useEffect(() => {
    if (!contextMenu) return;
    const close = () => setContextMenu(null);
    window.addEventListener("click", close);
    window.addEventListener("scroll", close, true);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("scroll", close, true);
    };
  }, [contextMenu]);

  const occupied = useMemo(() => {
    const set = new Set(folders?.subscription_folders ?? []);
    if (folders?.direct_folder) set.add(folders.direct_folder);
    return set;
  }, [folders]);

  const emptyFolders = useMemo(
    () => new Set(folders?.empty_folders ?? []),
    [folders],
  );

  const children = useMemo(() => {
    const options = new Set(filterTreePaths(folders?.items ?? [], occupied));
    if (value) options.add(value);
    return buildFolderTree(options);
  }, [folders, value, occupied]);

  const rootNode = useMemo<FolderTreeNode>(
    () => ({
      name: t("library.dataRoot"),
      path: DATA_ROOT_PATH,
      children,
    }),
    [children, t],
  );

  const sharedCount = folders?.shared_folders[value] ?? 0;

  const handleToggle = (path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const openContextMenu = (event: MouseEvent, path: string) => {
    event.preventDefault();
    event.stopPropagation();
    const canCreate = canCreateChild(path, occupied);
    const canManage = canManageFolder(path, occupied, emptyFolders);
    if (!canCreate && !canManage) return;
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      path,
      canCreate,
      canManage,
    });
  };

  const startCreate = (parentPath: string) => {
    setActionError(null);
    setInlineEdit({ mode: "create", parentPath, name: "" });
    if (parentPath) {
      setExpanded((prev) => new Set(prev).add(parentPath));
    }
    setContextMenu(null);
  };

  const startRename = (path: string) => {
    const name = path.includes("/")
      ? path.slice(path.lastIndexOf("/") + 1)
      : path;
    setActionError(null);
    setInlineEdit({ mode: "rename", path, name });
    setContextMenu(null);
  };

  const commitInline = async () => {
    if (!inlineEdit) return;
    const segment = inlineEdit.name.trim().replace(/[/\\]+/g, "");
    if (!segment) {
      setInlineEdit(null);
      return;
    }

    setBusy(true);
    setActionError(null);

    if (inlineEdit.mode === "create") {
      const target = joinFolderPath(inlineEdit.parentPath, segment);
      const result = await createLibraryFolder(target);
      setBusy(false);
      if ("error" in result) {
        setActionError(result.error);
        return;
      }
      setInlineEdit(null);
      await refresh();
      onChange(result.path);
      setExpanded((prev) => {
        const next = new Set(prev);
        for (const path of pathAncestors(result.path)) next.add(path);
        return next;
      });
      return;
    }

    const result = await renameLibraryFolder(inlineEdit.path, segment);
    setBusy(false);
    if ("error" in result) {
      setActionError(result.error);
      return;
    }
    const oldPath = inlineEdit.path;
    setInlineEdit(null);
    await refresh();
    if (value === oldPath || value.startsWith(`${oldPath}/`)) {
      onChange(
        value === oldPath
          ? result.path
          : `${result.path}${value.slice(oldPath.length)}`,
      );
    }
  };

  const handleDelete = async (path: string) => {
    setBusy(true);
    setActionError(null);
    const result = await deleteLibraryFolder(path);
    setBusy(false);
    if ("error" in result) {
      setActionError(result.error);
      return;
    }
    await refresh();
    if (value === path || value.startsWith(`${path}/`)) {
      onChange("");
    }
  };

  return (
    <>
      <div className="flex flex-col gap-2">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-foreground-600 text-sm">{label}</span>
          {value ? (
            <span className="text-foreground-400 max-w-[70%] truncate font-mono text-xs">
              {value}
            </span>
          ) : null}
        </div>

        <div
          className={`border-default-200 max-h-56 overflow-y-auto rounded-lg border ${
            isDisabled ? "pointer-events-none opacity-50" : ""
          }`}
        >
          <ul className="m-0 list-none p-1">
            <TreeRow
              node={rootNode}
              depth={0}
              selected={value}
              expanded={expanded}
              occupied={occupied}
              emptyFolders={emptyFolders}
              inlineEdit={inlineEdit}
              busy={busy}
              isRoot
              onToggle={handleToggle}
              onSelect={onChange}
              onContextMenu={openContextMenu}
              onInlineNameChange={(name) => {
                setInlineEdit((prev) => (prev ? { ...prev, name } : prev));
              }}
              onInlineCommit={() => {
                void commitInline();
              }}
              onInlineCancel={() => setInlineEdit(null)}
              isDisabled={isDisabled}
            />
          </ul>
        </div>

        {sharedCount > 1 && (
          <p className="text-warning text-xs">
            {t("library.sharedHint", { count: sharedCount })}
          </p>
        )}

        {actionError && <p className="text-danger text-xs">{actionError}</p>}

        {contextMenu && (
          <div
            className="border-default-200 bg-content1 fixed z-[100] min-w-[10rem] rounded-lg border py-1 shadow-lg"
            style={{ left: contextMenu.x, top: contextMenu.y }}
            onClick={(event) => event.stopPropagation()}
          >
            {contextMenu.canCreate && (
              <button
                type="button"
                className="hover:bg-default-100 w-full px-3 py-2 text-left text-sm"
                onClick={() => startCreate(contextMenu.path)}
              >
                {t("library.contextNewChild")}
              </button>
            )}
            {contextMenu.canManage && (
              <>
                <button
                  type="button"
                  className="hover:bg-default-100 w-full px-3 py-2 text-left text-sm"
                  onClick={() => startRename(contextMenu.path)}
                >
                  {t("library.contextRename")}
                </button>
                <button
                  type="button"
                  className="hover:bg-danger-50 text-danger w-full px-3 py-2 text-left text-sm"
                  onClick={() => {
                    setDeletePath(contextMenu.path);
                    setContextMenu(null);
                  }}
                >
                  {t("library.contextDelete")}
                </button>
              </>
            )}
          </div>
        )}
      </div>
      <ConfirmationModal
        isOpen={deletePath !== null}
        message={t("library.deleteConfirm", { path: deletePath ?? "" })}
        confirmColor="danger"
        isBusy={busy}
        onClose={() => setDeletePath(null)}
        onConfirm={() => {
          if (!deletePath) return;
          void handleDelete(deletePath).then(() => setDeletePath(null));
        }}
      />
    </>
  );
}
