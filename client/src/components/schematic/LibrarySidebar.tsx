import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Input, Sidebar, Text } from "../UI";

export interface LibrarySearchHit {
  lib_id: string;
  name: string;
  library: string;
  description: string;
  footprint_filter: string;
  reference: string;
  value: string;
  footprint: string;
  datasheet: string;
  mpn: string;
  is_power: boolean;
  score: number;
}

export interface LibrarySidebarProps {
  /** kiserver base URL. Defaults to `/api/server` (gateway path). */
  apiBase?: string;
  /** Required when the gateway needs a project_id (for cached
   *  indexes); defaults to the first opened project on the page. */
  projectId?: string;
  /** Optional fetch override — tests inject a stub. */
  fetcher?: typeof fetch;
  /** Notify parent when the user starts dragging a symbol so the
   *  canvas can render a snap preview. */
  onDragSymbolStart?: (hit: LibrarySearchHit) => void;
  /** Notify parent when the drag ends (drop or cancel). */
  onDragSymbolEnd?: () => void;
}

type LoadStatus = "idle" | "loading" | "ready" | "error";

const STATUS_COLOR: Record<LoadStatus, string> = {
  ready: "text-emerald-500",
  error: "text-red-500",
  loading: "text-amber-500",
  idle: "text-[var(--text)]/60",
};

/**
 * Searchable symbol library sidebar (M1-T-02). The search bar fires
 * a debounced GET to the gateway's
 * `/api/server/project/{id}/library/search?q=…` endpoint and lists
 * the ranked results. Each row is HTML5-draggable; the canvas's
 * drop handler reads the `application/x-kiclaude-lib-id` payload to
 * call `ui_symbol_place_xy`.
 */
export function LibrarySidebar(props: LibrarySidebarProps) {
  const {
    apiBase = "/api/server",
    projectId,
    fetcher,
    onDragSymbolStart,
    onDragSymbolEnd,
  } = props;

  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<LibrarySearchHit[]>([]);
  const [status, setStatus] = useState<LoadStatus>("idle");
  const [error, setError] = useState<string | null>(null);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fetchImpl = useMemo(() => fetcher ?? globalThis.fetch.bind(globalThis), [fetcher]);

  const runSearch = useCallback(
    async (q: string) => {
      if (!projectId) {
        setStatus("error");
        setError("no project_id");
        setHits([]);
        return;
      }
      setStatus("loading");
      setError(null);
      try {
        const url = new URL(
          `${apiBase}/project/${encodeURIComponent(projectId)}/library/search`,
          window.location.origin,
        );
        url.searchParams.set("q", q);
        url.searchParams.set("limit", "50");
        const resp = await fetchImpl(url.toString());
        if (!resp.ok) {
          throw new Error(`${resp.status} ${resp.statusText}`);
        }
        const body = (await resp.json()) as { hits?: LibrarySearchHit[] };
        setHits(body.hits ?? []);
        setStatus("ready");
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
        setHits([]);
      }
    },
    [apiBase, projectId, fetchImpl],
  );

  useEffect(() => {
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      runSearch(query);
    }, 120);
    return () => {
      if (debounce.current) clearTimeout(debounce.current);
    };
  }, [query, runSearch]);

  function onDragStart(e: React.DragEvent<HTMLLIElement>, hit: LibrarySearchHit) {
    e.dataTransfer.setData("application/x-kiclaude-lib-id", hit.lib_id);
    e.dataTransfer.setData(
      "application/x-kiclaude-symbol-hit",
      JSON.stringify(hit),
    );
    e.dataTransfer.effectAllowed = "copy";
    onDragSymbolStart?.(hit);
  }

  function onDragEnd() {
    onDragSymbolEnd?.();
  }

  return (
    <Sidebar
      data-testid="library-sidebar"
      aria-label="kiclaude symbol library"
      edge="left"
      width="17.5rem"
      open
      flush
      title={
        <div className="flex items-center justify-between">
          <Text variant="h4">Library</Text>
          <span
            data-testid="library-status"
            className={`text-[11px] ${STATUS_COLOR[status]}`}
          >
            {status === "loading" ? "…" : status}
          </span>
        </div>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-2 p-2">
        <Input
          inputSize="sm"
          data-testid="library-search-input"
          placeholder="Search symbols…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {status === "error" ? (
          <p
            data-testid="library-error"
            className="m-0 px-1.5 py-1 text-xs text-red-600 dark:text-red-400"
          >
            {error}
          </p>
        ) : null}
        <ul
          data-testid="library-results"
          className="m-0 min-h-0 flex-1 list-none overflow-y-auto p-0"
        >
          {hits.map((hit) => (
            <li
              key={hit.lib_id}
              data-testid={`library-hit-${hit.lib_id}`}
              draggable
              onDragStart={(e) => onDragStart(e, hit)}
              onDragEnd={onDragEnd}
              className="mb-0.5 cursor-grab rounded-md border border-[var(--border)] bg-[var(--code-bg)] p-1.5 hover:border-[var(--accent-border)]"
            >
              <div className="flex justify-between">
                <strong className="text-sm text-[var(--text-h)]">{hit.name}</strong>
                <span className="text-[11px] text-[var(--text)]/60">
                  {hit.library}
                </span>
              </div>
              {hit.description ? (
                <div className="text-[11px] text-[var(--text)]/70">
                  {hit.description}
                </div>
              ) : null}
              {hit.footprint ? (
                <div className="font-mono text-[10px] text-[var(--text)]/50">
                  {hit.footprint}
                </div>
              ) : null}
            </li>
          ))}
          {hits.length === 0 && status === "ready" ? (
            <li
              data-testid="library-empty"
              className="p-2 italic text-[var(--text)]/60"
            >
              no matches
            </li>
          ) : null}
        </ul>
      </div>
    </Sidebar>
  );
}
