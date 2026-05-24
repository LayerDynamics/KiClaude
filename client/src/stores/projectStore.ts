/**
 * `projectStore` — the currently-open kiclaude project, plus its
 * lifecycle status. Mirrors the on-disk `kcir::Project` shape via the
 * `KcirProject` type below.
 *
 * Loaded by the wasm bootstrap (M0-T-02) once a directory is opened
 * through the kiserver round-trip or the File System Access API.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";

export interface KcirProjectMetadata {
  title: string;
  revision: string;
  company: string;
  date: string;
}

export interface KcirNet {
  name: string;
  power_rail?: string | null;
}

export interface KcirFootprintInstance {
  uuid: string;
  refdes: string;
  lib_id: string;
  value: string;
  position_mm: [number, number];
  rotation_deg: number;
  locked: boolean;
}

export interface KcirTrack {
  uuid: string;
  net: string;
  width_mm: number;
  points_mm: Array<[number, number]>;
}

export interface KcirPcb {
  version: number;
  generator: string;
  layers: Array<{ id: number; name: string; kind: string }>;
  footprints: KcirFootprintInstance[];
  tracks: KcirTrack[];
  vias: unknown[];
  zones: unknown[];
  nets: KcirNet[];
}

export interface KcirStackupLayer {
  name: string;
  /** One of `copper | dielectric | soldermask | silkscreen | paste | adhesive` —
   * mirrors the KCIR `StackupLayerKind` enum in
   * `crates/ki/src/kcir/stackup.rs`. */
  kind: string;
  thickness_mm: number;
  dielectric_constant: number | null;
  loss_tangent: number | null;
  /** Material name for dielectrics (`"FR4"`) or render hint for copper
   * (`"copper"`). Round-trips with KiCad's `(material …)` line. */
  color: string;
}

export interface KcirStackup {
  layers: KcirStackupLayer[];
  power_plane_layers: string[];
  controlled_impedance: boolean;
  /** Sum of `layer.thickness_mm` — server-side recomputed on every
   * `ui_stackup_set`; never set by the UI. */
  board_thickness_mm: number;
  /** `HASL`, `ENIG`, `OSP`, … — empty string when unset. */
  finish: string;
}

export interface KcirProject {
  kcir_version: string;
  name: string;
  pcb: KcirPcb;
  metadata: KcirProjectMetadata;
  net_classes: Array<{ name: string; clearance_mm: number; trace_width_mm: number }>;
  /** M3-R-01 stackup model — populated from the project's `.kicad_pcb`
   * `(setup (stackup …))` block on load, edited via `ui_stackup_set`. */
  stackup?: KcirStackup;
}

export type ProjectStatus = "idle" | "loading" | "ready" | "error";

interface ProjectState {
  project: KcirProject | null;
  projectId: string | null;
  rootPath: string | null;
  status: ProjectStatus;
  error: string | null;
  setProject: (project: KcirProject, opts?: { projectId?: string; rootPath?: string }) => void;
  clear: () => void;
  setError: (error: string) => void;
  setStatus: (status: ProjectStatus) => void;
}

export const useProjectStore = create<ProjectState>()(
  devtools(
    (set) => ({
      project: null,
      projectId: null,
      rootPath: null,
      status: "idle",
      error: null,
      setProject(project, opts) {
        set(() => ({
          project,
          projectId: opts?.projectId ?? null,
          rootPath: opts?.rootPath ?? null,
          status: "ready",
          error: null,
        }));
      },
      clear() {
        set(() => ({
          project: null,
          projectId: null,
          rootPath: null,
          status: "idle",
          error: null,
        }));
      },
      setError(error) {
        set(() => ({ status: "error", error }));
      },
      setStatus(status) {
        set(() => ({ status }));
      },
    }),
    { name: "projectStore" },
  ),
);
