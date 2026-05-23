export interface SelectionRect {
  /** Top-left x in container-relative pixels. */
  x: number;
  /** Top-left y in container-relative pixels. */
  y: number;
  width: number;
  height: number;
  /** Optional label drawn at the top-left of the rect. */
  label?: string;
}

export interface SelectionOverlayProps {
  rects: SelectionRect[];
  /** A live rubber-band rectangle the user is dragging. */
  rubber?: SelectionRect | null;
  /** Container height so the SVG sizes itself correctly. */
  height: number;
}

/**
 * Pure-SVG overlay drawn on top of the kicanvas embed. Renders the
 * persistent selection set (filled blue) plus the in-flight rubber-
 * band rectangle (dashed grey). Zero-cost when both are empty.
 */
export function SelectionOverlay({
  rects,
  rubber,
  height,
}: SelectionOverlayProps) {
  if (rects.length === 0 && (!rubber || (rubber.width === 0 && rubber.height === 0))) {
    return null;
  }
  return (
    <svg
      data-testid="schematic-selection-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height,
        pointerEvents: "none",
      }}
    >
      {rects.map((rect, i) => (
        <g key={`${rect.x}-${rect.y}-${rect.width}-${rect.height}-${i}`}>
          <rect
            x={rect.x}
            y={rect.y}
            width={Math.max(0, rect.width)}
            height={Math.max(0, rect.height)}
            fill="rgba(99, 179, 237, 0.18)"
            stroke="#63b3ed"
            strokeWidth={1.5}
            data-testid="selection-rect"
          />
          {rect.label ? (
            <text
              x={rect.x + 4}
              y={Math.max(rect.y - 4, 12)}
              fill="#bee3f8"
              fontSize={10}
              fontFamily="ui-monospace, SFMono-Regular, monospace"
            >
              {rect.label}
            </text>
          ) : null}
        </g>
      ))}
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
          data-testid="rubber-band"
        />
      ) : null}
    </svg>
  );
}
