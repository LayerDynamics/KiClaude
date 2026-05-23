import { useCallback, useState } from "react";

export interface JunctionToolApi {
  /** Last gateway error. */
  error: string | null;
  /** True while a POST is in flight. */
  pending: boolean;
  /** Place a junction marker at the given pixel coordinate. */
  placeJunction: (x: number, y: number) => Promise<string | null>;
}

export interface JunctionToolProps {
  projectId: string;
  sheetUuid?: string;
  apiBase?: string;
  fetcher?: typeof fetch;
  onJunctionSaved?: (
    junctionUuid: string,
    position: [number, number],
  ) => void;
}

/**
 * `useJunctionTool` (M1-T-03) — one-click junction placement.
 *
 * Each call rounds the pixel coordinate to integer mm so the
 * propagation graph treats co-incident junctions as the same node.
 * The pixel → mm conversion is done by the caller; this hook just
 * forwards the supplied coordinate to `ui_junction_place_xy`.
 */
export function useJunctionTool(props: JunctionToolProps): JunctionToolApi {
  const {
    projectId,
    sheetUuid,
    apiBase = "/api/ui",
    fetcher,
    onJunctionSaved,
  } = props;

  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const placeJunction = useCallback(
    async (x: number, y: number): Promise<string | null> => {
      setPending(true);
      setError(null);
      try {
        const url = `${apiBase}/ui_junction_place_xy/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              sheet_uuid: sheetUuid ?? "",
              position_mm: [x, y],
            },
          }),
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          junction_uuid?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.junction_uuid) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        onJunctionSaved?.(body.junction_uuid, [x, y]);
        return body.junction_uuid;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return null;
      } finally {
        setPending(false);
      }
    },
    [apiBase, fetchImpl, onJunctionSaved, projectId, sheetUuid],
  );

  return { error, pending, placeJunction };
}

export interface JunctionToolMarkerProps {
  /** All visible junction positions in pixel space. */
  positions: Array<[number, number]>;
  height: number;
}

/** SVG overlay rendering recently placed junctions until the next
 *  full KCIR refresh. */
export function JunctionToolMarkers({
  positions,
  height,
}: JunctionToolMarkerProps) {
  if (positions.length === 0) return null;
  return (
    <svg
      data-testid="junction-tool-overlay"
      style={{
        position: "absolute",
        inset: 0,
        width: "100%",
        height,
        pointerEvents: "none",
      }}
    >
      {positions.map(([x, y], i) => (
        <circle
          key={`${x}-${y}-${i}`}
          cx={x}
          cy={y}
          r={3.5}
          fill="#f56565"
          stroke="#fff5f5"
          strokeWidth={1}
          data-testid="junction-tool-marker"
        />
      ))}
    </svg>
  );
}
