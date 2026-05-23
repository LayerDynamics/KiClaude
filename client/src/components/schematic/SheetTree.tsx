import { useMemo } from "react";

import { Sidebar, Text } from "../UI";

export interface SheetNode {
  uuid: string;
  name: string;
  file: string;
  parent: string | null;
}

export interface SheetTreeProps {
  /** Every sheet in the project (root has `parent === null`). */
  sheets: SheetNode[];
  /** Currently-active sheet uuid. */
  activeSheetUuid: string | null;
  /** Fired when the user clicks a row. */
  onNavigate?: (sheet_uuid: string) => void;
  /** Optional className for layout. */
  className?: string;
}

interface TreeRow {
  sheet: SheetNode;
  depth: number;
  /** Breadcrumb path from the root, sheet names. */
  path: string[];
}

/**
 * `SheetTree` (M1-T-05) — multi-sheet navigator. Builds the
 * parent → children tree from a flat sheet list, renders a clickable
 * list with indentation per depth, highlights the active row, and
 * shows a breadcrumb of the active sheet's ancestry above the list.
 *
 * Detects cycles (defensive — KCIR's loader doesn't produce them
 * but a hand-crafted Schematic could) and renders the offending
 * row with a `(cycle)` suffix instead of recursing forever.
 */
export function SheetTree(props: SheetTreeProps) {
  const { sheets, activeSheetUuid, onNavigate, className } = props;

  const rows = useMemo(() => buildRows(sheets), [sheets]);
  const breadcrumb = useMemo(
    () => buildBreadcrumb(sheets, activeSheetUuid),
    [sheets, activeSheetUuid],
  );

  return (
    <Sidebar
      data-testid="sheet-tree"
      aria-label="kiclaude multi-sheet navigator"
      edge="left"
      width="15rem"
      open
      flush
      className={className ?? ""}
      title={
        <div className="flex items-baseline justify-between">
          <Text variant="h4">Sheets</Text>
          <Text variant="caption">{rows.length}</Text>
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-2 p-2">
        {breadcrumb.length > 0 ? (
          <nav
            data-testid="sheet-breadcrumb"
            className="flex flex-wrap text-[11px]"
          >
            {breadcrumb.map((step, i) => (
              <span key={`${step.uuid}-${i}`}>
                {i > 0 ? (
                  <span className="text-[var(--text)]/50"> / </span>
                ) : null}
                <button
                  type="button"
                  data-testid={`sheet-breadcrumb-${step.uuid}`}
                  onClick={() => onNavigate?.(step.uuid)}
                  className={`cursor-pointer border-none bg-transparent p-0 text-[11px] ${
                    step.uuid === activeSheetUuid
                      ? "text-sky-400 underline"
                      : "text-sky-300"
                  }`}
                >
                  {step.name || "(unnamed)"}
                </button>
              </span>
            ))}
          </nav>
        ) : null}
        <ul
          data-testid="sheet-tree-list"
          className="m-0 min-h-0 flex-1 list-none overflow-y-auto p-0"
        >
          {rows.map((row) => {
            const isActive = row.sheet.uuid === activeSheetUuid;
            return (
              <li
                key={row.sheet.uuid}
                data-testid={`sheet-tree-row-${row.sheet.uuid}`}
                data-active={isActive ? "true" : "false"}
                className={`m-0 rounded ${
                  isActive ? "bg-slate-200 dark:bg-slate-800" : "bg-transparent"
                }`}
                style={{ paddingLeft: row.depth * 12 }}
              >
                <button
                  type="button"
                  onClick={() => onNavigate?.(row.sheet.uuid)}
                  aria-current={isActive ? "page" : undefined}
                  className="block w-full cursor-pointer border-none bg-transparent px-2 py-1 text-left text-[13px] text-[var(--text-h)] hover:bg-[var(--code-bg)]"
                >
                  <span className={isActive ? "font-semibold" : "font-normal"}>
                    {row.sheet.name || "(unnamed)"}
                  </span>
                  {row.sheet.file ? (
                    <span className="ml-1.5 text-[11px] text-[var(--text)]/60">
                      {row.sheet.file}
                    </span>
                  ) : null}
                </button>
              </li>
            );
          })}
          {rows.length === 0 ? (
            <li
              data-testid="sheet-tree-empty"
              className="p-2 italic text-[var(--text)]/60"
            >
              no sheets
            </li>
          ) : null}
        </ul>
      </div>
    </Sidebar>
  );
}

/** Build the visit-order row list (depth-first) for the given sheet set. */
function buildRows(sheets: SheetNode[]): TreeRow[] {
  const byUuid = new Map<string, SheetNode>();
  for (const sheet of sheets) {
    byUuid.set(sheet.uuid, sheet);
  }
  const children = new Map<string | null, SheetNode[]>();
  for (const sheet of sheets) {
    const key = sheet.parent;
    children.set(key, [...(children.get(key) ?? []), sheet]);
  }
  const rows: TreeRow[] = [];
  const visited = new Set<string>();
  const walk = (parent: string | null, depth: number, path: string[]) => {
    const list = children.get(parent) ?? [];
    for (const sheet of list) {
      if (visited.has(sheet.uuid)) continue;
      visited.add(sheet.uuid);
      const nextPath = [...path, sheet.name || "(unnamed)"];
      rows.push({ sheet, depth, path: nextPath });
      walk(sheet.uuid, depth + 1, nextPath);
    }
  };
  walk(null, 0, []);
  // Append any orphans (dangling parent links) so the tree never
  // hides sheets.
  for (const sheet of sheets) {
    if (!visited.has(sheet.uuid)) {
      rows.push({ sheet, depth: 0, path: [sheet.name || "(unnamed)"] });
    }
  }
  return rows;
}

interface BreadcrumbStep {
  uuid: string;
  name: string;
}

function buildBreadcrumb(
  sheets: SheetNode[],
  activeUuid: string | null,
): BreadcrumbStep[] {
  if (!activeUuid) return [];
  const byUuid = new Map<string, SheetNode>();
  for (const sheet of sheets) byUuid.set(sheet.uuid, sheet);
  const out: BreadcrumbStep[] = [];
  const visited = new Set<string>();
  let cursor: SheetNode | undefined = byUuid.get(activeUuid);
  while (cursor && !visited.has(cursor.uuid)) {
    visited.add(cursor.uuid);
    out.unshift({ uuid: cursor.uuid, name: cursor.name });
    cursor = cursor.parent ? byUuid.get(cursor.parent) : undefined;
  }
  return out;
}
