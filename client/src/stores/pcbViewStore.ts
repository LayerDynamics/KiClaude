/**
 * `pcbViewStore` — UI-only state for the PCB editor viewport: which
 * layer is currently active for new edits, which layers are visible,
 * and their per-layer opacity. Kept separate from
 * [`projectStore`](./projectStore.ts) so the heavy KCIR data isn't
 * re-shipped to subscribers on every toggle of a checkbox.
 *
 * State here is **not** persisted to `.kicad_pro` (the project file
 * carries its own layer enable/colour blocks); the M2-T-08 layer
 * panel task handles persistence by writing back through the REST
 * `/api/ui/layer_visibility` endpoint when the user clicks save.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";

/**
 * Minimal layer descriptor the PCB-view store carries — `id` is the
 * KiCad numeric layer id (e.g. `0` for `F.Cu`, `31` for `B.Cu`) and
 * `name` is the human-readable name (`F.Cu`, `B.Cu`, `Edge.Cuts`, …).
 * `kind` mirrors the `kcir::LayerKind` discriminant so the layer
 * panel can group conductor / mask / silk / fabrication layers.
 */
export interface PcbLayer {
  id: number;
  name: string;
  kind: string;
}

export interface PcbLayerView {
  visible: boolean;
  /** 0..1, where 1.0 = fully opaque. */
  opacity: number;
}

interface PcbViewState {
  layers: PcbLayer[];
  /** Per-layer-id view state. Layers not in this map default to
   *  `{ visible: true, opacity: 1 }`. */
  layerView: Record<number, PcbLayerView>;
  /** Per-layer-id colour overrides (`#RRGGBB`) — persisted into
   *  `.kicad_pro` via `ui_layer_color_set` (M2-T-08). Layers not
   *  in this map fall back to the deterministic-from-name swatch
   *  in `LayerStack.layerColour`. */
  layerColors: Record<number, string>;
  /** The currently-active layer for new edits (footprint pad layer,
   *  new track layer, etc.). `null` if no PCB is loaded. */
  activeLayerId: number | null;
  setLayers: (layers: PcbLayer[]) => void;
  setActiveLayer: (id: number) => void;
  /** Cycle the active layer by `delta` positions through `layers`
   *  (wraps). Used by the PgUp/PgDn hotkeys. */
  cycleActiveLayer: (delta: number) => void;
  toggleLayerVisible: (id: number) => void;
  setLayerOpacity: (id: number, opacity: number) => void;
  /** Set the per-layer colour. Caller is responsible for the
   *  server round-trip via `ui_layer_color_set`; this store only
   *  carries the working-copy state. */
  setLayerColor: (id: number, hex: string) => void;
  /** Bulk replace the colour map — used by the project loader
   *  when a project's `.kicad_pro` colours come into view. */
  setLayerColors: (colors: Record<number, string>) => void;
  /** Drag-reorder: move `id` to the position currently held by
   *  `targetId`. Layer order matters for visual stacking order.
   *  Refuses the move when the source or target is `F.Cu`/`B.Cu`
   *  (KiCad's stackup anchors), or when the two layers belong to
   *  different kinds (a silkscreen layer can't slot between two
   *  copper layers). Returns `true` on a successful move. */
  reorderLayer: (id: number, targetId: number) => boolean;
}

const DEFAULT_VIEW: PcbLayerView = { visible: true, opacity: 1 };

const FIXED_STACKUP_NAMES = new Set(["F.Cu", "B.Cu"]);
const HEX_RE = /^#[0-9a-fA-F]{6}$/;

export const usePcbViewStore = create<PcbViewState>()(
  devtools(
    (set, get) => ({
      layers: [],
      layerView: {},
      layerColors: {},
      activeLayerId: null,
      setLayers(layers) {
        set((state) => {
          // Preserve any per-layer view + colour overrides for layer
          // ids that survive the new list; reset the rest.
          const survivingView: Record<number, PcbLayerView> = {};
          const survivingColors: Record<number, string> = {};
          for (const layer of layers) {
            survivingView[layer.id] = state.layerView[layer.id] ?? DEFAULT_VIEW;
            if (state.layerColors[layer.id] !== undefined) {
              survivingColors[layer.id] = state.layerColors[layer.id]!;
            }
          }
          const active =
            state.activeLayerId != null &&
            layers.some((l) => l.id === state.activeLayerId)
              ? state.activeLayerId
              : (layers.find((l) => l.kind === "copper")?.id ?? layers[0]?.id ?? null);
          return {
            layers,
            layerView: survivingView,
            layerColors: survivingColors,
            activeLayerId: active,
          };
        });
      },
      setActiveLayer(id) {
        const { layers } = get();
        if (!layers.some((l) => l.id === id)) return;
        set(() => ({ activeLayerId: id }));
      },
      cycleActiveLayer(delta) {
        const { layers, activeLayerId } = get();
        if (layers.length === 0) return;
        const startIdx = Math.max(
          0,
          layers.findIndex((l) => l.id === activeLayerId),
        );
        const len = layers.length;
        // JS `%` returns negative results for negative operands —
        // normalise into `[0, len)`.
        const nextIdx = ((startIdx + delta) % len + len) % len;
        const nextLayer = layers[nextIdx];
        if (nextLayer) {
          set(() => ({ activeLayerId: nextLayer.id }));
        }
      },
      toggleLayerVisible(id) {
        set((state) => {
          const current = state.layerView[id] ?? DEFAULT_VIEW;
          return {
            layerView: {
              ...state.layerView,
              [id]: { ...current, visible: !current.visible },
            },
          };
        });
      },
      setLayerOpacity(id, opacity) {
        const clamped = Math.min(1, Math.max(0, opacity));
        set((state) => {
          const current = state.layerView[id] ?? DEFAULT_VIEW;
          return {
            layerView: {
              ...state.layerView,
              [id]: { ...current, opacity: clamped },
            },
          };
        });
      },
      setLayerColor(id, hex) {
        if (!HEX_RE.test(hex)) return;
        set((state) => ({
          layerColors: { ...state.layerColors, [id]: hex.toLowerCase() },
        }));
      },
      setLayerColors(colors) {
        // Filter to entries with valid `#RRGGBB` strings so the
        // store can't be polluted by a malformed project file.
        const filtered: Record<number, string> = {};
        for (const [k, v] of Object.entries(colors)) {
          const numericId = Number.parseInt(k, 10);
          if (Number.isFinite(numericId) && typeof v === "string" && HEX_RE.test(v)) {
            filtered[numericId] = v.toLowerCase();
          }
        }
        set(() => ({ layerColors: filtered }));
      },
      reorderLayer(id, targetId) {
        if (id === targetId) return false;
        const state = get();
        const from = state.layers.findIndex((l) => l.id === id);
        const to = state.layers.findIndex((l) => l.id === targetId);
        if (from < 0 || to < 0) return false;
        const src = state.layers[from]!;
        const tgt = state.layers[to]!;
        // KiCad stackup anchors — F.Cu / B.Cu can never move.
        if (
          FIXED_STACKUP_NAMES.has(src.name) ||
          FIXED_STACKUP_NAMES.has(tgt.name)
        ) {
          return false;
        }
        // A layer can only swap with another of the same kind so
        // pcbnew's stackup model (all copper layers contiguous,
        // silkscreen pairs around them, etc.) is preserved.
        if (src.kind !== tgt.kind) {
          return false;
        }
        const next = state.layers.slice();
        const [moved] = next.splice(from, 1);
        if (!moved) return false;
        next.splice(to, 0, moved);
        set(() => ({ layers: next }));
        return true;
      },
    }),
    { name: "pcbViewStore" },
  ),
);

/** Look up the per-layer view, with defaults for unknown ids. */
export function getLayerView(
  state: Pick<PcbViewState, "layerView">,
  id: number,
): PcbLayerView {
  return state.layerView[id] ?? DEFAULT_VIEW;
}
