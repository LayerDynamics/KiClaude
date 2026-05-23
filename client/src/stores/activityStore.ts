/**
 * `activityStore` — append-only log of MCP tool calls + lifecycle
 * events surfaced from the agent service over the WebSocket. Persists
 * to localStorage so a page reload keeps the recent history visible
 * (used by the M1-T-08 ActivityJournal + originally by the M0-Q-05
 * hook-emission debugger).
 *
 * Entries are keyed by `id` so a `tool_use_end` frame can finalize a
 * previously-appended `tool_use_start` row without producing a
 * duplicate.
 */

import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";

export type ActivityStatus = "running" | "ok" | "error" | "denied";

export interface ActivityEntry {
  /** Stable per-call id. For Claude-driven calls this is the SDK's
   * `tool_use_id`; for direct UI tools the gateway mints a uuid. */
  id: string;
  /** UTC ISO-8601 string from the agent hook. */
  ts: string;
  /** Tool name (`kc_*` or `ui_*`). */
  tool_name: string;
  /** `true` for mutating tools that produced a snapshot we can revert
   * to. Read-only tools (`kc_kcir_get`, `kc_validate`, ...) are not
   * shown in the M1-T-08 journal. */
  mutating: boolean;
  /** Lifecycle bucket — `running` until `tool_use_end` arrives. */
  status: ActivityStatus;
  /** Snapshot id captured *before* the mutation ran. The journal's
   * "revert" button POSTs this back to
   * `/api/server/project/{id}/snapshot/revert`. */
  snapshot_id?: string;
  /** `true` once the journal posted a successful revert for this row. */
  reverted?: boolean;
  /** Parsed tool input (the `args` object). */
  input?: Record<string, unknown>;
  /** Tool response payload (the `structured` field of the MCP
   * envelope, or the raw JSON body for `ui_*` tools). */
  output?: Record<string, unknown>;
  /** Optional error message for `status === "error" | "denied"`. */
  error?: string;
  /** Wall-clock duration in ms (filled by `tool_use_end`). */
  duration_ms?: number;
  /** SDK session id from the agent's `SessionStart` hook. */
  session_id?: string;
  /** Project id the call ran against. */
  project_id?: string | null;
  /** Any extra fields the agent forwarded that we don't model. */
  extra?: Record<string, unknown>;
  /** Legacy field kept for M0 hook-debug compatibility. */
  event?: string;
}

interface ActivityState {
  entries: ActivityEntry[];
  /** Soft cap. Older entries roll off the front to keep localStorage
   * small. The journal still scrolls forever; the cap only governs
   * how far back a reload can see. */
  maxEntries: number;
  /** Append a fresh entry, or replace an existing one with the same
   * `id`. The journal calls this on `tool_use_start`. */
  append: (entry: ActivityEntry) => void;
  /** Merge a finalization (status/output/duration) onto an existing
   * `id` — called on `tool_use_end`. If no row matches, the patch is
   * applied as a fresh entry. */
  finalize: (patch: Partial<ActivityEntry> & { id: string }) => void;
  /** Mark an entry as reverted. */
  markReverted: (id: string) => void;
  clear: () => void;
  setMaxEntries: (max: number) => void;
}

export const useActivityStore = create<ActivityState>()(
  devtools(
    persist(
      (set) => ({
        entries: [],
        maxEntries: 500,
        append(entry) {
          set((state) => {
            const next = [...state.entries];
            const id = entry.id;
            const idx =
              id != null && id !== ""
                ? next.findIndex((e) => e.id === id)
                : -1;
            if (idx >= 0) {
              next[idx] = { ...next[idx], ...entry };
            } else {
              next.push(entry);
            }
            if (next.length > state.maxEntries) {
              next.splice(0, next.length - state.maxEntries);
            }
            return { entries: next };
          });
        },
        finalize(patch) {
          set((state) => {
            const idx = state.entries.findIndex((e) => e.id === patch.id);
            if (idx < 0) {
              const created: ActivityEntry = {
                ts: new Date().toISOString(),
                tool_name: "<unknown>",
                mutating: false,
                status: "ok",
                ...patch,
              };
              const next = [...state.entries, created];
              if (next.length > state.maxEntries) {
                next.splice(0, next.length - state.maxEntries);
              }
              return { entries: next };
            }
            const merged: ActivityEntry = {
              ...state.entries[idx]!,
              ...patch,
            };
            const next = [...state.entries];
            next[idx] = merged;
            return { entries: next };
          });
        },
        markReverted(id) {
          set((state) => {
            const idx = state.entries.findIndex((e) => e.id === id);
            if (idx < 0) return state;
            const next = [...state.entries];
            next[idx] = { ...next[idx]!, reverted: true };
            return { entries: next };
          });
        },
        clear() {
          set(() => ({ entries: [] }));
        },
        setMaxEntries(maxEntries) {
          set(() => ({ maxEntries }));
        },
      }),
      { name: "kiclaude.activity", version: 2 },
    ),
    { name: "activityStore" },
  ),
);

/** Tool names that ALWAYS produce a journal-eligible entry. Used by
 * the journal subscriber to filter `tool_use_start` frames the agent
 * forwards. The list mirrors the mutating-tool catalog in
 * `services/agent/src/agent/hooks/permission.py`. */
export const MUTATING_TOOLS = [
  "kc_symbol_add",
  "kc_symbol_edit",
  "kc_wire_connect",
  "kc_label_attach",
  "kc_project_save",
  "kc_snapshot_create",
  "kc_snapshot_revert",
  "ui_symbol_place_xy",
  "ui_wire_draw_points",
  "ui_label_place_xy",
  "ui_junction_place_xy",
  "ui_symbol_edit_props",
] as const;

const MUTATING_PREFIXES = ["kc_symbol_", "kc_wire_", "kc_label_", "kc_snapshot_", "ui_"];
const MUTATING_EXACT = new Set<string>([...MUTATING_TOOLS, "kc_project_save"]);

export function isMutatingTool(name: string): boolean {
  if (!name) return false;
  if (MUTATING_EXACT.has(name)) return true;
  return MUTATING_PREFIXES.some((p) => name.startsWith(p));
}
