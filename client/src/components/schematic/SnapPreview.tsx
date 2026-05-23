export interface SnapAnchor {
  /** Snap-to-grid position in container-relative pixels. */
  x: number;
  y: number;
  /** Optional grid pitch used to draw the alignment cross-hairs. */
  grid_mm?: number;
  /** Optional label (e.g. `"R1 → (50.8, 50.8) mm"`). */
  label?: string;
}

export interface SnapPreviewProps {
  anchor: SnapAnchor | null;
  height: number;
}

/**
 * Cross-hair + label drawn at the user's snap target while they drag
 * a symbol onto the canvas. Hidden when `anchor` is null.
 */
export function SnapPreview({ anchor, height }: SnapPreviewProps) {
  if (!anchor) return null;
  const radius = 8;
  return (
    <svg
      data-testid="schematic-snap-preview"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height,
        pointerEvents: "none",
      }}
    >
      <line
        x1={anchor.x - radius}
        y1={anchor.y}
        x2={anchor.x + radius}
        y2={anchor.y}
        stroke="#f6ad55"
        strokeWidth={1.5}
      />
      <line
        x1={anchor.x}
        y1={anchor.y - radius}
        x2={anchor.x}
        y2={anchor.y + radius}
        stroke="#f6ad55"
        strokeWidth={1.5}
      />
      <circle
        cx={anchor.x}
        cy={anchor.y}
        r={radius / 2}
        fill="none"
        stroke="#f6ad55"
        strokeWidth={1.5}
        data-testid="snap-marker"
      />
      {anchor.label ? (
        <text
          x={anchor.x + radius + 4}
          y={anchor.y + 4}
          fill="#fbd38d"
          fontSize={11}
          fontFamily="ui-monospace, SFMono-Regular, monospace"
        >
          {anchor.label}
        </text>
      ) : null}
    </svg>
  );
}
