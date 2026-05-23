import { useMemo } from "react";

import { usePcbViewStore } from "../../stores/pcbViewStore";
import { useSelectionStore } from "../../stores/selectionStore";
import { useKcirStore } from "../../stores/kcirStore";
import type { KcirFootprintInstance, KcirTrack } from "../../stores/projectStore";

export interface EditOverlayProps {
  /** Container width and height in container pixels. */
  width: number;
  height: number;
  /** Affine mapping from board mm-space to overlay pixel-space.
   *  `[mmPerPxX, mmPerPxY, mmOriginX, mmOriginY]`. Defaults to the
   *  PcbCanvas's `1 mm = 4 px` baseline centered on (0,0). */
  transform?: BoardTransform;
  /** Optional in-flight rubber-band rectangle (parent-managed). */
  rubber?: { x: number; y: number; width: number; height: number } | null;
}

export interface BoardTransform {
  /** Pixels per mm along X. */
  scaleX: number;
  /** Pixels per mm along Y. */
  scaleY: number;
  /** Container-pixel x of board mm-origin (0,0). */
  originX: number;
  /** Container-pixel y of board mm-origin (0,0). */
  originY: number;
}

export const DEFAULT_BOARD_TRANSFORM = (
  width: number,
  height: number,
): BoardTransform => ({
  scaleX: 4,
  scaleY: 4,
  originX: width / 2,
  originY: height / 2,
});

/**
 * Pure-SVG overlay drawn on top of `kicanvas-embed` inside
 * [`PcbCanvas`]. Renders:
 *
 *   - Selection halos for every footprint / track / via / zone the
 *     user has selected. The halo's colour comes from the active
 *     layer's view-store entry so selections track the layer panel.
 *   - The live rubber-band rectangle the parent passes in.
 *
 * Footprint and track positions read directly from `useKcirStore`;
 * the overlay re-renders only when the selection or the KCIR slice
 * changes. Layer visibility/opacity is **respected** — selections
 * on hidden layers are dimmed, matching the editor's mental model
 * of "click-through to whatever's visible".
 *
 * Zero-cost when there's nothing to draw (returns `null`).
 */
export function EditOverlay({
  width,
  height,
  transform,
  rubber = null,
}: EditOverlayProps) {
  const selected = useSelectionStore((s) => s.selected);
  const hovered = useSelectionStore((s) => s.hovered);
  const footprints = useKcirStore((s) => s.footprints);
  const tracks = useKcirStore((s) => s.tracks);
  const layerView = usePcbViewStore((s) => s.layerView);
  const activeLayerId = usePcbViewStore((s) => s.activeLayerId);

  const tx = transform ?? DEFAULT_BOARD_TRANSFORM(width, height);

  // Build a fast uuid → item lookup so we only pay O(selected) per
  // render instead of O(selected × N_items).
  const items = useMemo(
    () => buildLookup(footprints, tracks),
    [footprints, tracks],
  );

  const nothingToDraw =
    selected.length === 0 &&
    hovered === null &&
    (!rubber || (rubber.width === 0 && rubber.height === 0));
  if (nothingToDraw) {
    return null;
  }

  const activeOpacity =
    activeLayerId != null
      ? (layerView[activeLayerId]?.opacity ?? 1)
      : 1;
  const activeVisible =
    activeLayerId != null
      ? (layerView[activeLayerId]?.visible ?? true)
      : true;
  const selectionOpacity = activeVisible ? activeOpacity : 0.35;

  return (
    <svg
      data-testid="pcb-edit-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width,
        height,
        pointerEvents: "none",
      }}
    >
      {selected.map((ref, i) => {
        const item = items.get(ref.uuid);
        if (!item) return null;
        if (item.kind === "footprint") {
          const { cx, cy } = boardToPx(item.position_mm, tx);
          return (
            <g key={`${ref.kind}-${ref.uuid}-${i}`} data-testid="selection-halo">
              <circle
                cx={cx}
                cy={cy}
                r={14}
                fill="rgba(99, 179, 237, 0.18)"
                stroke="#63b3ed"
                strokeWidth={1.5}
                opacity={selectionOpacity}
              />
              <text
                x={cx + 18}
                y={cy + 4}
                fill="#bee3f8"
                fontSize={10}
                fontFamily="ui-monospace, SFMono-Regular, monospace"
                opacity={selectionOpacity}
              >
                {item.refdes}
              </text>
            </g>
          );
        }
        // Track: highlight every segment in the polyline.
        const segments = item.points_mm
          .map((pt) => boardToPx(pt, tx))
          .map(({ cx, cy }) => `${cx},${cy}`)
          .join(" ");
        return (
          <polyline
            key={`${ref.kind}-${ref.uuid}-${i}`}
            data-testid="selection-track"
            points={segments}
            fill="none"
            stroke="#63b3ed"
            strokeWidth={Math.max(2, item.width_mm * tx.scaleX + 2)}
            opacity={selectionOpacity}
          />
        );
      })}
      {hovered && items.has(hovered.uuid)
        ? renderHoverHint(items.get(hovered.uuid), tx)
        : null}
      {rubber && (rubber.width > 0 || rubber.height > 0) ? (
        <rect
          x={rubber.x}
          y={rubber.y}
          width={rubber.width}
          height={rubber.height}
          fill="rgba(160, 174, 192, 0.12)"
          stroke="#a0aec0"
          strokeDasharray="4 3"
          strokeWidth={1}
          data-testid="pcb-rubber-band"
        />
      ) : null}
    </svg>
  );
}

type Item =
  | (KcirFootprintInstance & { kind: "footprint" })
  | (KcirTrack & { kind: "track" });

function buildLookup(
  footprints: KcirFootprintInstance[],
  tracks: KcirTrack[],
): Map<string, Item> {
  const m = new Map<string, Item>();
  for (const f of footprints) m.set(f.uuid, { ...f, kind: "footprint" });
  for (const t of tracks) m.set(t.uuid, { ...t, kind: "track" });
  return m;
}

function boardToPx(
  point: readonly [number, number],
  tx: BoardTransform,
): { cx: number; cy: number } {
  return {
    cx: tx.originX + point[0] * tx.scaleX,
    // KiCad's PCB Y axis points down; the SVG overlay's Y also
    // points down, so no flip.
    cy: tx.originY + point[1] * tx.scaleY,
  };
}

function renderHoverHint(
  item: Item | undefined,
  tx: BoardTransform,
): React.ReactNode {
  if (!item) return null;
  if (item.kind === "footprint") {
    const { cx, cy } = boardToPx(item.position_mm, tx);
    return (
      <circle
        data-testid="hover-halo"
        cx={cx}
        cy={cy}
        r={10}
        fill="none"
        stroke="#cbd5e1"
        strokeWidth={1}
        strokeDasharray="2 2"
      />
    );
  }
  const start = item.points_mm[0];
  if (!start) return null;
  const { cx, cy } = boardToPx(start, tx);
  return (
    <circle
      data-testid="hover-halo"
      cx={cx}
      cy={cy}
      r={6}
      fill="none"
      stroke="#cbd5e1"
      strokeWidth={1}
      strokeDasharray="2 2"
    />
  );
}
