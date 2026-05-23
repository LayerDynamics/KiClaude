import { useCallback, useState } from "react";

export type LabelKind = "local" | "global" | "hierarchical" | "power";

export interface LabelToolApi {
  /** Last gateway error. */
  error: string | null;
  /** True while a POST is in flight. */
  pending: boolean;
  /** Place a label at the given pixel coordinate. */
  placeLabel: (args: {
    x: number;
    y: number;
    text: string;
    kind?: LabelKind;
    rotation_deg?: number;
    shape?: string;
  }) => Promise<string | null>;
}

export interface LabelToolProps {
  projectId: string;
  sheetUuid?: string;
  apiBase?: string;
  fetcher?: typeof fetch;
  onLabelSaved?: (
    labelUuid: string,
    args: { text: string; kind: LabelKind; position: [number, number] },
  ) => void;
}

/**
 * `useLabelTool` (M1-T-03) — point-and-type label placement. Used by
 * the schematic editor when the user picks the label tool and clicks
 * a wire endpoint.
 *
 * Default `kind` is `"local"`; the wire-tools UI swaps to "global"
 * / "hierarchical" via the property strip. The hook forwards the
 * full set including `shape` (for hierarchical inputs/outputs/etc.).
 */
export function useLabelTool(props: LabelToolProps): LabelToolApi {
  const {
    projectId,
    sheetUuid,
    apiBase = "/api/ui",
    fetcher,
    onLabelSaved,
  } = props;

  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const placeLabel = useCallback(
    async ({
      x,
      y,
      text,
      kind = "local",
      rotation_deg = 0,
      shape = "",
    }: {
      x: number;
      y: number;
      text: string;
      kind?: LabelKind;
      rotation_deg?: number;
      shape?: string;
    }): Promise<string | null> => {
      const trimmed = (text ?? "").trim();
      if (!trimmed) {
        setError("label text is required");
        return null;
      }
      setPending(true);
      setError(null);
      try {
        const url = `${apiBase}/ui_label_place_xy/${encodeURIComponent(projectId)}`;
        const resp = await fetchImpl(url, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            args: {
              sheet_uuid: sheetUuid ?? "",
              kind,
              text: trimmed,
              position_mm: [x, y],
              rotation_deg,
              shape,
            },
          }),
        });
        const body = (await resp.json()) as {
          ok?: boolean;
          label_uuid?: string;
          error?: string;
        };
        if (!resp.ok || !body.ok || !body.label_uuid) {
          throw new Error(body.error ?? `${resp.status} ${resp.statusText}`);
        }
        onLabelSaved?.(body.label_uuid, {
          text: trimmed,
          kind,
          position: [x, y],
        });
        return body.label_uuid;
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return null;
      } finally {
        setPending(false);
      }
    },
    [apiBase, fetchImpl, onLabelSaved, projectId, sheetUuid],
  );

  return { error, pending, placeLabel };
}
