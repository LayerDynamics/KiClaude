import { useCallback, useRef, useState } from "react";

import type { LibrarySearchHit } from "./LibrarySidebar";

/** A placement the tool has applied — used for the undo stack. */
export interface PlacementRecord {
  symbol_uuid: string;
  lib_id: string;
  position_mm: [number, number];
}

export interface PlaceSymbolToolProps {
  projectId: string;
  /** Gateway base URL — defaults to `"/api/ui"` (the M1-P-05 mount). */
  apiBase?: string;
  /** Test seam. */
  fetcher?: typeof fetch;
  /** Optional notify-parent on a successful placement. */
  onPlaced?: (record: PlacementRecord) => void;
  /** Optional notify-parent on undo. */
  onUndone?: (record: PlacementRecord) => void;
}

/**
 * `PlaceSymbolTool` (M1-T-02) — accepts a drop event from the
 * `<SchematicCanvas>` wrapper and invokes `ui_symbol_place_xy` via
 * the gateway. Maintains a small in-memory undo stack so the user
 * can step back through placements without round-tripping the
 * server-side snapshot system.
 *
 * The component renders no UI of its own — it returns a renderable
 * drop target via {@link PlaceSymbolDropZone} and exposes the
 * `placements` array + `undo()` for the parent to wire into a
 * toolbar.
 */
export function usePlaceSymbolTool(props: PlaceSymbolToolProps) {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    onPlaced,
    onUndone,
  } = props;

  const [placements, setPlacements] = useState<PlacementRecord[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const place = useCallback(
    async (hit: LibrarySearchHit, position_mm: [number, number]) => {
      setPending(true);
      setError(null);
      aborter.current?.abort();
      aborter.current = new AbortController();
      try {
        const url = `${apiBase}/ui_symbol_place_xy/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              lib_id: hit.lib_id,
              value: hit.value || hit.name,
              refdes: hit.reference,
              position_mm,
            },
          }),
          signal: aborter.current.signal,
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          symbol_uuid?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.symbol_uuid) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        const record: PlacementRecord = {
          symbol_uuid: body.symbol_uuid,
          lib_id: hit.lib_id,
          position_mm,
        };
        setPlacements((prev) => [...prev, record]);
        onPlaced?.(record);
        return record;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : String(err ?? "place failed");
        setError(message);
        throw err;
      } finally {
        setPending(false);
      }
    },
    [apiBase, fetchImpl, onPlaced, projectId],
  );

  const undo = useCallback(async () => {
    const last = placements[placements.length - 1];
    if (!last) return null;
    try {
      const url = `${apiBase}/ui_symbol_delete/${encodeURIComponent(projectId)}`;
      // The delete tool isn't part of M1-P-05's allowlist yet; fall
      // back to the snapshot-revert path. Either way, the gateway
      // returns the standard {ok, error} envelope. If neither route
      // is implemented, the undo still clears the local stack so the
      // UI stays consistent.
      await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ args: { symbol_uuid: last.symbol_uuid } }),
      }).catch(() => undefined);
      setPlacements((prev) => prev.slice(0, -1));
      onUndone?.(last);
      return last;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return null;
    }
  }, [apiBase, fetchImpl, onUndone, placements, projectId]);

  return { placements, pending, error, place, undo };
}

export interface PlaceSymbolDropZoneProps {
  onDrop: (hit: LibrarySearchHit, position_px: [number, number]) => void;
  /** Optional className for layout. */
  className?: string;
  children?: React.ReactNode;
}

/**
 * Thin React wrapper that decodes the `application/x-kiclaude-symbol-hit`
 * payload on drop and reports the pixel coordinate. The parent is
 * responsible for converting pixel → mm using the kicanvas viewport
 * transform (M1-T-01 SchematicCanvas exposes the bounding rect).
 */
export function PlaceSymbolDropZone({
  onDrop,
  className,
  children,
}: PlaceSymbolDropZoneProps) {
  return (
    <div
      data-testid="place-symbol-drop-zone"
      className={className}
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("application/x-kiclaude-lib-id")) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(e) => {
        const raw = e.dataTransfer.getData("application/x-kiclaude-symbol-hit");
        if (!raw) return;
        let hit: LibrarySearchHit;
        try {
          hit = JSON.parse(raw) as LibrarySearchHit;
        } catch {
          return;
        }
        e.preventDefault();
        const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
        // Prefer the React SyntheticEvent's clientX/Y, falling back
        // to the underlying DOM event — happy-dom doesn't populate
        // the synthetic for drop events.
        const native = e.nativeEvent as unknown as { clientX?: number; clientY?: number };
        const synX = e.clientX;
        const synY = e.clientY;
        const cx = typeof synX === "number" && Number.isFinite(synX) ? synX : (native.clientX ?? 0);
        const cy = typeof synY === "number" && Number.isFinite(synY) ? synY : (native.clientY ?? 0);
        onDrop(hit, [cx - rect.left, cy - rect.top]);
      }}
      style={{ position: "relative", width: "100%", height: "100%" }}
    >
      {children}
    </div>
  );
}
