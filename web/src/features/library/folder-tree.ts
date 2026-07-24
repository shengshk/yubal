export type FolderTreeNode = {
  name: string;
  path: string;
  children: FolderTreeNode[];
};

export const MAX_FOLDER_DEPTH = 3;

/** Build a sorted folder tree from flat relative paths (`a/b/c`). */
export function buildFolderTree(paths: Iterable<string>): FolderTreeNode[] {
  const root: FolderTreeNode[] = [];
  const byPath = new Map<string, FolderTreeNode>();

  const ensure = (path: string): FolderTreeNode => {
    const existing = byPath.get(path);
    if (existing) return existing;

    const slash = path.lastIndexOf("/");
    const name = slash >= 0 ? path.slice(slash + 1) : path;
    const node: FolderTreeNode = { name, path, children: [] };
    byPath.set(path, node);

    if (slash >= 0) {
      ensure(path.slice(0, slash)).children.push(node);
    } else {
      root.push(node);
    }
    return node;
  };

  for (const path of paths) {
    const trimmed = path.trim();
    if (!trimmed) continue;
    ensure(trimmed);
  }

  const sortNodes = (nodes: FolderTreeNode[]) => {
    nodes.sort((a, b) =>
      a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
    );
    for (const child of nodes) sortNodes(child.children);
  };
  sortNodes(root);
  return root;
}

/** Ancestors of a path, including itself (e.g. `a/b/c` → `a`, `a/b`, `a/b/c`). */
export function pathAncestors(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  const out: string[] = [];
  for (let i = 0; i < parts.length; i++) {
    out.push(parts.slice(0, i + 1).join("/"));
  }
  return out;
}

export function pathDepth(path: string): number {
  return path.split("/").filter(Boolean).length;
}

export type FolderRole = "occupied" | "ancestor" | "idle";

/** Whether `path` lies strictly inside a subscription save folder. */
export function isInsideOccupied(
  path: string,
  occupied: ReadonlySet<string>,
): boolean {
  for (const folder of occupied) {
    if (path !== folder && path.startsWith(`${folder}/`)) return true;
  }
  return false;
}

export function filterTreePaths(
  paths: Iterable<string>,
  occupied: ReadonlySet<string>,
): string[] {
  const out: string[] = [];
  for (const path of paths) {
    const trimmed = path.trim();
    if (!trimmed) continue;
    if (isInsideOccupied(trimmed, occupied)) continue;
    out.push(trimmed);
  }
  return out;
}

export function getFolderRole(
  path: string,
  occupied: ReadonlySet<string>,
): FolderRole {
  if (occupied.has(path)) return "occupied";
  for (const folder of occupied) {
    if (folder.startsWith(`${path}/`)) return "ancestor";
  }
  return "idle";
}

export function canSelectFolder(
  path: string,
  occupied: ReadonlySet<string>,
): boolean {
  return getFolderRole(path, occupied) !== "ancestor";
}

export function canExpandFolder(
  path: string,
  occupied: ReadonlySet<string>,
  hasChildNodes: boolean,
): boolean {
  const role = getFolderRole(path, occupied);
  if (role === "occupied") return false;
  return role === "ancestor" || hasChildNodes;
}

export function canCreateChild(
  parentPath: string,
  occupied: ReadonlySet<string>,
): boolean {
  if (!parentPath) return true;
  const role = getFolderRole(parentPath, occupied);
  if (role === "occupied") return false;
  return pathDepth(parentPath) < MAX_FOLDER_DEPTH;
}

export function canManageFolder(
  path: string,
  occupied: ReadonlySet<string>,
  emptyFolders: ReadonlySet<string>,
): boolean {
  if (!path) return false;
  if (getFolderRole(path, occupied) !== "idle") return false;
  return emptyFolders.has(path);
}

export function joinFolderPath(parent: string, segment: string): string {
  const name = segment.trim().replace(/^\/+|\/+$/g, "");
  if (!name) return parent;
  return parent ? `${parent}/${name}` : name;
}

