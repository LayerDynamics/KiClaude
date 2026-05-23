import { useCallback } from "react";

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
export function LayerStack({ className, height }: LayerStackProps) {
  const layers = usePcbViewStore((s) => s.layers);
  const activeLayerId = usePcbViewStore((s) => s.activeLayerId);
  const layerView = usePcbViewStore((s) => s.layerView);
  const setActiveLayer = usePcbViewStore((s) => s.setActiveLayer);
  const toggleLayerVisible = usePcbViewStore((s) => s.toggleLayerVisible);
  const setLayerOpacity = usePcbViewStore((s) => s.setLayerOpacity);
  const reorderLayer = usePcbViewStore((s) => s.reorderLayer);

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
    (e: React.DragEvent<HTMLLIElement>, targetId: number) => {
      e.preventDefault();
      const raw = e.dataTransfer.getData("application/x-kiclaude-layer-id");
      const sourceId = Number.parseInt(raw, 10);
      if (Number.isFinite(sourceId)) {
        reorderLayer(sourceId, targetId);
      }
    },
    [reorderLayer],
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
          return (
            <li
              key={layer.id}
              data-testid="layer-row"
              data-layer-id={layer.id}
              data-active={isActive ? "true" : "false"}
              draggable
              onDragStart={(e) => handleDragStart(e, layer.id)}
              onDragOver={handleDragOver}
              onDrop={(e) => handleDrop(e, layer.id)}
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
                <span style={{ ...colourDotStyle, background: layerColour(layer) }} />
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
  width: 12,
  height: 12,
  borderRadius: 3,
  display: "inline-block",
  border: "1px solid rgba(255,255,255,0.15)",
  flex: "0 0 auto",
};

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
