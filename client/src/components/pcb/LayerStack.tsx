import { useCallback } from "react";

import { useProjectStore } from "../../stores/projectStore";
import {
  getLayerView,
  usePcbViewStore,
  type PcbLayer,
} from "../../stores/pcbViewStore";

export interface LayerStackProps {
  /** Optional className for the outer container. */
  className?: string;
  /** Fixed pixel height; `undefined` lets the parent govern. */
  height?: number;
  /** Gateway base URL — defaults to `/api/ui`. Used by the M2-T-08
   *  colour-picker + reorder server round-trips. */
  apiBase?: string;
  /** Test seam — defaults to `globalThis.fetch`. */
  fetcher?: typeof fetch;
}

/**
 * M2-T-01 / M2-T-08 layer panel.
 *
 * Lists every layer carried by the current `kcir::Pcb`, with:
 *   - a click target that promotes the row to the active layer
 *   - a visibility toggle (drives the [`EditOverlay`] alpha)
 *   - an opacity slider (0..100 %)
 *   - drag-and-drop reorder among adjacent rows
 *
 * The component is **controlled entirely by [`usePcbViewStore`]** —
 * the parent `PcbCanvas` doesn't pass layer state through props. That
 * keeps the panel useable from any pane (sidebar, dialog) without
 * threading callbacks.
 *
 * Per-layer **colour pickers** belong to the layer panel finaliser
 * (M2-T-08) and read from `.kicad_pro`; this M2-T-01 baseline ships
 * visibility + opacity + reorder. The DOM exposes
 * `data-testid="layer-stack"` and per-row `data-layer-id="N"` so
 * Playwright (`M2-Q-02`) can drive it directly.
 */
export function LayerStack({
  className,
  height,
  apiBase = "/api/ui",
  fetcher,
}: LayerStackProps) {
  const layers = usePcbViewStore((s) => s.layers);
  const activeLayerId = usePcbViewStore((s) => s.activeLayerId);
  const layerView = usePcbViewStore((s) => s.layerView);
  const layerColors = usePcbViewStore((s) => s.layerColors);
  const setActiveLayer = usePcbViewStore((s) => s.setActiveLayer);
  const toggleLayerVisible = usePcbViewStore((s) => s.toggleLayerVisible);
  const setLayerOpacity = usePcbViewStore((s) => s.setLayerOpacity);
  const setLayerColor = usePcbViewStore((s) => s.setLayerColor);
  const reorderLayer = usePcbViewStore((s) => s.reorderLayer);
  const projectId = useProjectStore((s) => s.projectId);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const handleDragStart = useCallback(
    (e: React.DragEvent<HTMLLIElement>, id: number) => {
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("application/x-kiclaude-layer-id", String(id));
    },
    [],
  );

  const handleDragOver = useCallback((e: React.DragEvent<HTMLLIElement>) => {
    if (e.dataTransfer.types.includes("application/x-kiclaude-layer-id")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
    }
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent<HTMLLIElement>, targetId: number) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/x-kiclaude-layer-id");
      const sourceId = Number.parseInt(raw, 10);
      if (!Number.isFinite(sourceId)) return;
      const moved = reorderLayer(sourceId, targetId);
      if (!moved || !projectId) return;
      // Server round-trip: persist the new order to .kicad_pro so a
      // reload keeps it. We fire-and-forget — the store-side reorder
      // already ran; the server is just informed.
      try {
        await fetchImpl(
          `${apiBase}/ui_layer_reorder/${encodeURIComponent(projectId)}`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              args: { layer_id: sourceId, target_id: targetId },
            }),
          },
        );
      } catch {
        // Network failure shouldn't roll back the local move — the
        // user can re-drag if persistence is genuinely required.
      }
    },
    [apiBase, fetchImpl, projectId, reorderLayer],
  );

  const handleColorChange = useCallback(
    async (id: number, hex: string) => {
      setLayerColor(id, hex);
      if (!projectId) return;
      try {
        await fetchImpl(
          `${apiBase}/ui_layer_color_set/${encodeURIComponent(projectId)}`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              args: { layer_id: id, color: hex },
            }),
          },
        );
      } catch {
        // Same policy as reorder — keep the local change visible
        // even if the server is unreachable.
      }
    },
    [apiBase, fetchImpl, projectId, setLayerColor],
  );

  if (layers.length === 0) {
    return (
      <div
        data-testid="layer-stack"
        data-status="empty"
        className={className}
        style={{ ...containerStyle, height }}
      >
        <p style={{ fontSize: 12, color: "#9ca3af", padding: 12, margin: 0 }}>
          No PCB loaded.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="layer-stack"
      data-status="ready"
      className={className}
      style={{ ...containerStyle, height }}
    >
      <header style={headerStyle}>Layers</header>
      <ul
        role="listbox"
        aria-label="PCB layers"
        style={listStyle}
      >
        {layers.map((layer) => {
          const view = getLayerView({ layerView }, layer.id);
          const isActive = activeLayerId === layer.id;
          const persistedColor = layerColors[layer.id];
          const effectiveColor = persistedColor ?? layerColour(layer);
          return (
            <li
              key={layer.id}
              data-testid="layer-row"
              data-layer-id={layer.id}
              data-active={isActive ? "true" : "false"}
              draggable
              onDragStart={(e) => handleDragStart(e, layer.id)}
              onDragOver={handleDragOver}
              onDrop={(e) => {
                void handleDrop(e, layer.id);
              }}
              role="option"
              aria-selected={isActive}
              style={rowStyle(isActive)}
            >
              <button
                type="button"
                onClick={() => setActiveLayer(layer.id)}
                style={activeButtonStyle(isActive, layer)}
                title={`${layer.name} (${layer.kind})`}
              >
                <label
                  style={{ ...colourDotStyle, background: effectiveColor }}
                  onClick={(e) => e.stopPropagation()}
                  data-testid="layer-color-swatch"
                  title={`${layer.name} colour`}
                >
                  <input
                    type="color"
                    value={hexFromCssColour(effectiveColor)}
                    onChange={(e) => {
                      void handleColorChange(layer.id, e.target.value);
                    }}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`Pick colour for ${layer.name}`}
                    data-testid="layer-color-picker"
                    style={colourPickerInputStyle}
                  />
                </label>
                <span style={{ flex: 1, textAlign: "left" }}>{layer.name}</span>
                <span style={kindBadgeStyle}>{layer.kind}</span>
              </button>
              <label
                style={visibilityLabelStyle}
                title={view.visible ? "Hide layer" : "Show layer"}
              >
                <input
                  type="checkbox"
                  checked={view.visible}
                  onChange={() => toggleLayerVisible(layer.id)}
                  aria-label={`Toggle visibility of ${layer.name}`}
                  data-testid="layer-visibility"
                />
              </label>
              <input
                type="range"
                min={0}
                max={100}
                value={Math.round(view.opacity * 100)}
                onChange={(e) =>
                  setLayerOpacity(layer.id, Number(e.target.value) / 100)
                }
                aria-label={`Opacity of ${layer.name}`}
                data-testid="layer-opacity"
                style={opacityRangeStyle}
                disabled={!view.visible}
              />
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Pick a deterministic display colour per layer name. Used only as
 *  a visual aid in this M2-T-01 baseline; M2-T-08 will replace this
 *  with the persisted `.kicad_pro` layer colour. */
function layerColour(layer: PcbLayer): string {
  switch (layer.name) {
    case "F.Cu":
      return "#c8362f";
    case "B.Cu":
      return "#2f73c8";
    case "F.SilkS":
    case "F.Silkscreen":
      return "#e2c870";
    case "B.SilkS":
    case "B.Silkscreen":
      return "#a08652";
    case "F.Mask":
      return "#7b3f7b";
    case "B.Mask":
      return "#4a2a4a";
    case "Edge.Cuts":
      return "#f0e68c";
    case "F.Paste":
      return "#a8a8a8";
    case "B.Paste":
      return "#606060";
    default:
      // Hash the name into a stable hue so distinct user layers stay
      // distinguishable across reloads.
      return `hsl(${hashHue(layer.name)} 60% 55%)`;
  }
}

function hashHue(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) >>> 0;
  }
  return h % 360;
}

const containerStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  background: "#10131a",
  border: "1px solid #1f2330",
  borderRadius: 6,
  overflow: "hidden",
  minWidth: 240,
};

const headerStyle: React.CSSProperties = {
  padding: "8px 12px",
  fontSize: 12,
  fontWeight: 600,
  letterSpacing: 0.4,
  textTransform: "uppercase",
  color: "#cbd5e1",
  borderBottom: "1px solid #1f2330",
  background: "#161b25",
};

const listStyle: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  overflowY: "auto",
  flex: 1,
};

function rowStyle(active: boolean): React.CSSProperties {
  return {
    display: "grid",
    gridTemplateColumns: "1fr auto 80px",
    alignItems: "center",
    gap: 8,
    padding: "6px 12px",
    borderBottom: "1px solid #1a1f2a",
    background: active ? "#1d2535" : "transparent",
    cursor: "grab",
  };
}

function activeButtonStyle(
  active: boolean,
  _layer: PcbLayer,
): React.CSSProperties {
  return {
    display: "flex",
    alignItems: "center",
    gap: 8,
    background: "transparent",
    border: "none",
    color: active ? "#f7fafc" : "#cbd5e1",
    fontWeight: active ? 600 : 400,
    fontSize: 13,
    cursor: "pointer",
    padding: 0,
    width: "100%",
  };
}

const colourDotStyle: React.CSSProperties = {
  width: 14,
  height: 14,
  borderRadius: 3,
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  border: "1px solid rgba(255,255,255,0.15)",
  flex: "0 0 auto",
  cursor: "pointer",
  position: "relative",
  overflow: "hidden",
};

const colourPickerInputStyle: React.CSSProperties = {
  // Stretch the native colour input across the swatch so the entire
  // dot acts as the trigger, but keep it invisible — the swatch
  // surface itself communicates the chosen colour.
  position: "absolute",
  inset: 0,
  width: "100%",
  height: "100%",
  opacity: 0,
  border: "none",
  padding: 0,
  cursor: "pointer",
  background: "transparent",
};

/** Normalise an HSL/CSS-named/hex colour to the `#rrggbb` shape that
 *  the native `<input type="color">` requires. Falls back to a safe
 *  black on parse failure so the picker can still open. */
function hexFromCssColour(value: string): string {
  if (/^#[0-9a-fA-F]{6}$/.test(value)) return value.toLowerCase();
  if (typeof document === "undefined") return "#000000";
  // Cheap browser-resident parser: assign to a div's style and read
  // the computed `getComputedStyle` colour (returns `rgb(...)`).
  const probe = document.createElement("div");
  probe.style.color = value;
  document.body.appendChild(probe);
  const rgb = getComputedStyle(probe).color;
  document.body.removeChild(probe);
  const m = /rgba?\((\d+)[, ]+(\d+)[, ]+(\d+)/.exec(rgb);
  if (!m) return "#000000";
  const toHex = (n: string) =>
    Number.parseInt(n, 10).toString(16).padStart(2, "0");
  return `#${toHex(m[1]!)}${toHex(m[2]!)}${toHex(m[3]!)}`.toLowerCase();
}

const kindBadgeStyle: React.CSSProperties = {
  fontSize: 10,
  color: "#9ca3af",
  textTransform: "lowercase",
  letterSpacing: 0.2,
};

const visibilityLabelStyle: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  cursor: "pointer",
};

const opacityRangeStyle: React.CSSProperties = {
  width: 80,
  accentColor: "#63b3ed",
};
