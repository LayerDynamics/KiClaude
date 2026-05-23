import { useCallback, useEffect, useRef, useState } from "react";

import { usePcbViewStore } from "../../../stores/pcbViewStore";

/** Coarse grid step (mm) the placement tool snaps drops to. */
const DEFAULT_GRID_MM = 0.5;

/** Coordinates supplied when the user drops a footprint. */
export interface FootprintDropPayload {
  /** KiCad lib-id, e.g. `Resistor_SMD:R_0603_1608Metric`. */
  lib_id: string;
  /** Optional reference designator hint from the library row. */
  refdes?: string;
  /** Default value text from the library row. */
  value?: string;
}

/** A placement the tool has applied — used for the undo stack. */
export interface FootprintPlacementRecord {
  footprint_uuid: string;
  lib_id: string;
  position_mm: [number, number];
  rotation_deg: number;
  layer: string;
}

export interface PlaceFootprintToolProps {
  projectId: string;
  /** Gateway base URL — defaults to `/api/ui` (the M2-P-05 mount). */
  apiBase?: string;
  /** Test seam. */
  fetcher?: typeof fetch;
  /** Snap step in millimetres. Defaults to 0.5 mm — KiCad's
   *  small-step grid. Set to `0` to disable snap. */
  gridMm?: number;
  /** Notify-parent on successful placement (refdes assigned). */
  onPlaced?: (record: FootprintPlacementRecord) => void;
  /** Notify-parent on undo. */
  onUndone?: (record: FootprintPlacementRecord) => void;
}

/**
 * `usePlaceFootprintTool` (M2-T-02) — drag-from-library footprint
 * placement state machine.
 *
 * Hotkeys (while a drag is in flight or a footprint was just dropped):
 *   - `R` — rotate the pending placement by +90° (held in
 *     `pendingRotation`; flushed to the next drop).
 *   - `F` — flip the active layer (`F.Cu` ↔ `B.Cu`); applies to the
 *     next drop.
 *   - `Esc` — cancel the pending drag and clear rotation/flip state.
 *
 * Drops snap to a `gridMm` grid (default 0.5 mm). The active layer
 * comes from [`usePcbViewStore`]'s `activeLayerId` so a single
 * source of truth governs both keyboard layer cycling (PcbCanvas
 * PgUp/PgDn) and the placement tool's destination layer.
 */
export function usePlaceFootprintTool(props: PlaceFootprintToolProps) {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    gridMm = DEFAULT_GRID_MM,
    onPlaced,
    onUndone,
  } = props;

  const layers = usePcbViewStore((s) => s.layers);
  const activeLayerId = usePcbViewStore((s) => s.activeLayerId);

  const [placements, setPlacements] = useState<FootprintPlacementRecord[]>([]);
  const [pendingRotation, setPendingRotation] = useState(0);
  /** When true, the tool flips the active layer on the NEXT drop —
   *  e.g. user pressed `F` mid-drag and now wants the footprint on
   *  the opposite copper side. Consumed once per drop. */
  const [pendingFlip, setPendingFlip] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const aborter = useRef<AbortController | null>(null);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  /** Layer name lookup: id → name (e.g. `0 → "F.Cu"`). */
  const layerName = useCallback(
    (id: number | null): string | null => {
      if (id == null) return null;
      const layer = layers.find((l) => l.id === id);
      return layer?.name ?? null;
    },
    [layers],
  );

  /** Compute the destination layer name for a drop, taking the
   *  `F` flip flag into account. Returns `null` if no layer is
   *  active — caller should refuse the drop in that case. */
  const computeDestinationLayer = useCallback((): string | null => {
    const base = layerName(activeLayerId);
    if (!base) return null;
    if (!pendingFlip) return base;
    if (base === "F.Cu") return "B.Cu";
    if (base === "B.Cu") return "F.Cu";
    if (base === "F.SilkS" || base === "F.Silkscreen") return "B.SilkS";
    if (base === "B.SilkS" || base === "B.Silkscreen") return "F.SilkS";
    // Default policy: flip a Front-prefixed name to Back, vice
    // versa; leave non-flippable layers (Edge.Cuts, In*.Cu) alone.
    if (base.startsWith("F.")) return `B.${base.slice(2)}`;
    if (base.startsWith("B.")) return `F.${base.slice(2)}`;
    return base;
  }, [activeLayerId, layerName, pendingFlip]);

  const rotate = useCallback(() => {
    setPendingRotation((r) => (r + 90) % 360);
  }, []);
  const flip = useCallback(() => {
    setPendingFlip((f) => !f);
  }, []);
  const cancel = useCallback(() => {
    setPendingRotation(0);
    setPendingFlip(false);
    setError(null);
    aborter.current?.abort();
  }, []);

  /** Snap a mm coordinate to the grid; preserves sign correctly
   *  (Math.round handles half-step boundaries the way KiCad does). */
  const snap = useCallback(
    (mm: number): number => {
      if (gridMm <= 0) return mm;
      return Math.round(mm / gridMm) * gridMm;
    },
    [gridMm],
  );

  const place = useCallback(
    async (
      payload: FootprintDropPayload,
      position_mm: [number, number],
    ) => {
      const destinationLayer = computeDestinationLayer();
      if (!destinationLayer) {
        setError("no active layer — open a project before placing");
        return null;
      }
      setPending(true);
      setError(null);
      aborter.current?.abort();
      aborter.current = new AbortController();
      const snapped: [number, number] = [snap(position_mm[0]), snap(position_mm[1])];
      const rotation_deg = pendingRotation;
      try {
        const url = `${apiBase}/ui_footprint_place_xy/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              lib_id: payload.lib_id,
              refdes: payload.refdes ?? "",
              value: payload.value ?? "",
              position_mm: snapped,
              rotation_deg,
              layer: destinationLayer,
            },
          }),
          signal: aborter.current.signal,
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          footprint_uuid?: string;
          refdes?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.footprint_uuid) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        const record: FootprintPlacementRecord = {
          footprint_uuid: body.footprint_uuid,
          lib_id: payload.lib_id,
          position_mm: snapped,
          rotation_deg,
          layer: destinationLayer,
        };
        setPlacements((prev) => [...prev, record]);
        // Successful placement consumes the pending flip but the
        // rotation persists — KiCad's behaviour, so a user dropping
        // ten 90°-rotated parts keeps R pressed once.
        setPendingFlip(false);
        onPlaced?.(record);
        return record;
      } catch (err) {
        const message =
          err instanceof Error ? err.message : String(err ?? "place failed");
        setError(message);
        return null;
      } finally {
        setPending(false);
      }
    },
    [
      apiBase,
      computeDestinationLayer,
      fetchImpl,
      onPlaced,
      pendingRotation,
      projectId,
      snap,
    ],
  );

  const undo = useCallback(async () => {
    const last = placements[placements.length - 1];
    if (!last) return null;
    // The `ui_footprint_delete` route is not part of M2-P-05;
    // fall back to a snapshot-revert at the user's discretion.
    // Locally pop the record so the UI stays consistent — the
    // server-side commit can be undone via the activity journal
    // (M1-T-08) which exposes per-call revert.
    setPlacements((prev) => prev.slice(0, -1));
    onUndone?.(last);
    return last;
  }, [onUndone, placements]);

  // Bind R/F/Esc on window so the user can press them during a
  // drag (the source element holds focus during HTML5 DnD).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't steal keys from inputs — the library sidebar has a
      // search box.
      const target = e.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      if (e.key === "r" || e.key === "R") {
        e.preventDefault();
        rotate();
      } else if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        flip();
      } else if (e.key === "Escape") {
        e.preventDefault();
        cancel();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rotate, flip, cancel]);

  return {
    placements,
    pendingRotation,
    pendingFlip,
    pending,
    error,
    rotate,
    flip,
    cancel,
    place,
    undo,
    snap,
    computeDestinationLayer,
  };
}

export interface PlaceFootprintDropZoneProps {
  /** Called when a footprint payload is dropped on this zone.
   *  `position_px` is container-relative pixels — the parent
   *  converts to mm via the PCB view transform. */
  onDrop: (
    payload: FootprintDropPayload,
    position_px: [number, number],
  ) => void;
  className?: string;
  children?: React.ReactNode;
}

/**
 * Drop target wrapper. Decodes the
 * `application/x-kiclaude-footprint-hit` payload the library sidebar
 * sets at the start of a drag.
 *
 * Falls back to `application/x-kiclaude-lib-id` (plain text lib-id)
 * for users dragging from external library tools.
 */
export function PlaceFootprintDropZone({
  onDrop,
  className,
  children,
}: PlaceFootprintDropZoneProps) {
  return (
    <div
      data-testid="place-footprint-drop-zone"
      className={className}
      onDragOver={(e) => {
        if (
          e.dataTransfer.types.includes("application/x-kiclaude-footprint-hit") ||
          e.dataTransfer.types.includes("application/x-kiclaude-lib-id")
        ) {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }
      }}
      onDrop={(e) => {
        let payload: FootprintDropPayload | null = null;
        const raw = e.dataTransfer.getData("application/x-kiclaude-footprint-hit");
        if (raw) {
          try {
            payload = JSON.parse(raw) as FootprintDropPayload;
          } catch {
            payload = null;
          }
        }
        if (!payload) {
          const lib = e.dataTransfer.getData("application/x-kiclaude-lib-id");
          if (lib) payload = { lib_id: lib };
        }
        if (!payload) return;
        e.preventDefault();
        const rect = (e.currentTarget as HTMLDivElement).getBoundingClientRect();
        const native = e.nativeEvent as unknown as {
          clientX?: number;
          clientY?: number;
        };
        const synX = e.clientX;
        const synY = e.clientY;
        const cx =
          typeof synX === "number" && Number.isFinite(synX) ? synX : (native.clientX ?? 0);
        const cy =
          typeof synY === "number" && Number.isFinite(synY) ? synY : (native.clientY ?? 0);
        onDrop(payload, [cx - rect.left, cy - rect.top]);
      }}
      style={{ position: "relative", width: "100%", height: "100%" }}
    >
      {children}
    </div>
  );
}
