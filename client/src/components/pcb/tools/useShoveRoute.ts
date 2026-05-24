/**
 * `useShoveRoute` (M3-T-05) — the push-and-shove route gesture.
 *
 * As the user drags a trace from a start pad to the cursor, this hook
 * runs the cad-crate push-and-shove router live (via the
 * `routeShove` wasm bridge from M3-R-03) and surfaces:
 *
 * - `plan.route_mm` — the proposed new track,
 * - `plan.moved` — every existing track that would be shoved aside,
 *   with its new geometry (the canvas ghosts these so the user sees
 *   the shove before committing),
 * - `fellBack` — when PnS can't place the route (a wall, a cycle, or
 *   the shove budget); the gesture then defers to the M2 walk-around
 *   router for that drag.
 *
 * `commit()` persists the plan: the new track + every moved track are
 * written through the `ui_shove_apply` gateway tool in one shot.
 *
 * The hook builds the `ShoveWorld` from the project's tracks + vias +
 * footprint pads on the active layer. Own-net items are still sent
 * (the Rust side excludes them by net), so switching the in-flight
 * net doesn't require rebuilding the world.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { loadKiclaudeWasm } from "../../../lib/wasm";
import { useProjectStore } from "../../../stores/projectStore";
import type { KcirProject } from "../../../stores/projectStore";

export interface ShoveMovedTrack {
  item_id: number;
  net: string;
  layer: string;
  width_mm: number;
  points_mm: Array<[number, number]>;
}

export type ShoveRouteResult =
  | {
      status: "routed";
      route_mm: Array<[number, number]>;
      moved: ShoveMovedTrack[];
      shoves_applied: number;
    }
  | { status: "fell_back"; reason: string }
  | { status: "invalid_input"; message: string };

interface ShoveWasm {
  routeShove(requestJson: string): string;
}

export interface UseShoveRouteOptions {
  projectId: string;
  apiBase?: string;
  fetcher?: typeof fetch;
  wasmLoader?: () => Promise<{ cad: ShoveWasm }>;
  /** Active copper layer the route is on. */
  layer: string;
  /** Net the in-flight track carries. */
  net: string;
  /** Track width (mm). */
  widthMm: number;
  /** Copper clearance (mm). */
  clearanceMm: number;
}

export interface ShoveRouteApi {
  start: [number, number] | null;
  cursor: [number, number] | null;
  /** The live PnS plan for the current start→cursor drag, or null
   * before both endpoints are set / before wasm loads. */
  plan: Extract<ShoveRouteResult, { status: "routed" }> | null;
  /** True when PnS couldn't place the current drag — the caller
   * should run the walk-around router for this segment instead. */
  fellBack: boolean;
  /** The PnS fall-back reason, for the status line. */
  fellBackReason: string | null;
  error: string | null;
  setStart: (p: [number, number] | null) => void;
  setCursor: (p: [number, number] | null) => void;
  /** Persist the current plan (new track + shoved tracks). Resolves
   * false when there's no committable plan. */
  commit: () => Promise<boolean>;
  /** Clear the in-flight gesture. */
  reset: () => void;
}

interface ShoveWorldJson {
  clearance_mm: number;
  items: unknown[];
}

/** The world plus the synthetic-id → project-track-uuid map so a
 * shoved track (reported by synthetic id) can be persisted back to
 * the right project row. */
export interface BuiltWorld {
  world: ShoveWorldJson;
  /** synthetic ItemId → project track uuid. Vias/pads omitted (they
   * never move). */
  idToUuid: Record<number, string>;
}

/** Build the `ShoveWorld` JSON the Rust router consumes from the
 * project's PCB items on `layer`. Tracks become shovable Track items;
 * vias + pads become fixed walls. */
export function buildShoveWorld(
  project: KcirProject,
  layer: string,
  clearanceMm: number,
): BuiltWorld {
  const items: unknown[] = [];
  const idToUuid: Record<number, string> = {};
  let nextId = 1;
  const tracks = (project.pcb.tracks ?? []) as Array<{
    uuid?: string;
    net: string;
    width_mm: number;
    points_mm: Array<[number, number]>;
    locked?: boolean;
    layer?: string;
  }>;
  for (const t of tracks) {
    // A track with no explicit layer is assumed to be on the active
    // layer (older KCIR rows). Skip tracks known to be on other layers.
    if (t.layer && t.layer !== layer) continue;
    items.push({
      // Rust `ItemId(pub u32)` is a serde newtype → serialises as a
      // bare number, so we emit the id as a plain integer.
      Track: {
        id: nextId,
        net: t.net,
        layer,
        width_mm: t.width_mm,
        points_mm: t.points_mm.map(([x, y]) => ({ x, y })),
        locked: Boolean(t.locked),
      },
    });
    if (t.uuid) idToUuid[nextId] = t.uuid;
    nextId += 1;
  }
  const vias = (project.pcb.vias ?? []) as Array<{
    net?: string;
    position_mm?: [number, number];
    diameter_mm?: number;
    from_layer?: string;
    to_layer?: string;
  }>;
  for (const v of vias) {
    if (!v.position_mm) continue;
    items.push({
      Via: {
        id: nextId,
        net: v.net ?? "",
        position_mm: { x: v.position_mm[0], y: v.position_mm[1] },
        diameter_mm: v.diameter_mm ?? 0.6,
        layers: [v.from_layer ?? layer, v.to_layer ?? layer],
      },
    });
    nextId += 1;
  }
  return { world: { clearance_mm: clearanceMm, items }, idToUuid };
}

export function useShoveRoute(opts: UseShoveRouteOptions): ShoveRouteApi {
  const {
    projectId,
    apiBase = "/api/ui",
    fetcher,
    wasmLoader = loadKiclaudeWasm as () => Promise<{ cad: ShoveWasm }>,
    layer,
    net,
    widthMm,
    clearanceMm,
  } = opts;
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const project = useProjectStore((s) => s.project);
  const [start, setStart] = useState<[number, number] | null>(null);
  const [cursor, setCursor] = useState<[number, number] | null>(null);
  const [plan, setPlan] = useState<
    Extract<ShoveRouteResult, { status: "routed" }> | null
  >(null);
  const [fellBack, setFellBack] = useState(false);
  const [fellBackReason, setFellBackReason] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Held in state (not a ref) so the plan effect below re-fires once
  // wasm finishes loading even if the drag endpoints were set first.
  const [wasm, setWasm] = useState<ShoveWasm | null>(null);
  useEffect(() => {
    let cancelled = false;
    void wasmLoader().then(
      (mod) => {
        if (!cancelled) setWasm(mod.cad);
      },
      (err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [wasmLoader]);

  const built = useMemo(
    () => (project ? buildShoveWorld(project, layer, clearanceMm) : null),
    [project, layer, clearanceMm],
  );
  const worldJson = useMemo(
    () => (built ? JSON.stringify(built.world) : null),
    [built],
  );

  // Re-run PnS whenever the drag endpoints, world, routing params,
  // or wasm-availability change.
  useEffect(() => {
    if (!wasm || !worldJson || !start || !cursor) {
      setPlan(null);
      setFellBack(false);
      setFellBackReason(null);
      return;
    }
    try {
      const request = JSON.stringify({
        world: JSON.parse(worldJson),
        input: {
          start_mm: { x: start[0], y: start[1] },
          end_mm: { x: cursor[0], y: cursor[1] },
          track_width_mm: widthMm,
          layer,
          net,
          clearance_mm: clearanceMm,
        },
      });
      const raw = wasm.routeShove(request);
      const result = JSON.parse(raw) as ShoveRouteResult;
      if (result.status === "routed") {
        setPlan(result);
        setFellBack(false);
        setFellBackReason(null);
      } else if (result.status === "fell_back") {
        setPlan(null);
        setFellBack(true);
        setFellBackReason(result.reason);
      } else {
        setPlan(null);
        setFellBack(false);
        setFellBackReason(null);
      }
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [wasm, worldJson, start, cursor, widthMm, layer, net, clearanceMm]);

  const commit = useCallback(async (): Promise<boolean> => {
    if (!plan || !built) return false;
    try {
      const url = `${apiBase}/ui_shove_apply/${encodeURIComponent(projectId)}`;
      const resp = await fetchImpl(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          args: {
            new_track: {
              net,
              layer,
              width_mm: widthMm,
              points_mm: plan.route_mm,
            },
            // Map each shoved track's synthetic id back to its
            // project uuid so the server updates the right row.
            // Tracks whose uuid we couldn't resolve are dropped (a
            // moved track with no project identity can't be persisted).
            moved_tracks: plan.moved
              .map((m) => ({
                uuid: built.idToUuid[m.item_id],
                points_mm: m.points_mm,
              }))
              .filter((m): m is { uuid: string; points_mm: Array<[number, number]> } =>
                Boolean(m.uuid),
              ),
          },
        }),
      });
      const body = (await resp.json()) as { ok?: boolean; error?: string; detail?: string };
      if (!resp.ok || !body.ok) {
        throw new Error(body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`);
      }
      setStart(null);
      setCursor(null);
      setPlan(null);
      return true;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      return false;
    }
  }, [apiBase, built, fetchImpl, layer, net, plan, projectId, widthMm]);

  const reset = useCallback(() => {
    setStart(null);
    setCursor(null);
    setPlan(null);
    setFellBack(false);
    setFellBackReason(null);
    setError(null);
  }, []);

  return {
    start,
    cursor,
    plan,
    fellBack,
    fellBackReason,
    error,
    setStart,
    setCursor,
    commit,
    reset,
  };
}
