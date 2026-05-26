/**
 * `loadThreeScene` — turn a [`ThreeScene`] (the JSON shape emitted by
 * `crates/cad/src/three_scene.rs::scene_from_pcb`) into a three.js
 * `Group` ready to mount under the viewer's root scene.
 *
 * Layout:
 *
 *   ThreeSceneGroup (board origin at world (0,0,0))
 *     ├── boardMesh    (Edge.Cuts polygon extruded by board_thickness_mm)
 *     └── placements   (one Group per ScenePlacement)
 *           ├── topMarker (BoxGeometry sized by scale, colored per side)
 *           └── refdesLabel? (handled by the React wrapper, not here)
 *
 * Why a placeholder box per placement instead of the real STEP mesh?
 * Loading STEP files requires OCCT in the browser (~20 MB wasm) and
 * cannot land before M3 ships. The plan note for M3-R-06 documents
 * this as the v1 deliverable; M4 swaps the box for the real
 * `occt-import-js` mesh without touching the layout contract here.
 * The marker boxes are colour-coded per side and sized from the
 * placement's `scale` so visual scan of the board still works.
 */

import {
  type BufferGeometry,
  BoxGeometry,
  Color,
  type ColorRepresentation,
  DoubleSide,
  ExtrudeGeometry,
  Group,
  type Material,
  Mesh,
  MeshStandardMaterial,
  Shape,
  Vector2,
} from "three";

import { decodeStep, mergeStepMeshes } from "./step";

/** Mirrors `crates/cad/src/three_scene.rs::ScenePlacement` after
 * `serde_json::to_string`. Tuples land in JS as `[number, number, number]`. */
export interface ScenePlacement {
  model_path: string;
  refdes: string;
  position_mm: [number, number, number];
  scale: [number, number, number];
  rotation_deg: [number, number, number];
  side: "top" | "bottom";
}

/** Mirrors `crates/cad/src/three_scene.rs::ThreeScene`. */
export interface ThreeScene {
  board_thickness_mm: number;
  board_outline_mm: Array<[number, number]>;
  placements: ScenePlacement[];
}

export interface SceneTheme {
  /** FR-4 green by default. */
  boardColor: ColorRepresentation;
  /** Tint for top-side placement markers. */
  topMarkerColor: ColorRepresentation;
  /** Tint for bottom-side placement markers. */
  bottomMarkerColor: ColorRepresentation;
}

export const DEFAULT_THEME: SceneTheme = {
  boardColor: 0x1f6f43,
  topMarkerColor: 0xfacc15, // amber — matches the M2 net inspector accent
  bottomMarkerColor: 0x60a5fa, // sky blue
};

/** Default marker dimensions in mm — used when the placement's
 * `scale` is at its unit default. Picked so a 1206 chip still
 * reads as a chip rather than a postage stamp. */
const DEFAULT_MARKER_LWH_MM = { length: 2.0, width: 1.2, height: 0.8 };

export interface LoadedScene {
  /** Three.js Group containing every mesh — mount under the viewer's
   * root scene with `viewer.add(group)`. */
  group: Group;
  /** Reference to the extruded board mesh — useful for downstream
   * picking, opacity tweaks, etc. `null` when no outline is set. */
  boardMesh: Mesh | null;
  /** One marker per placement, keyed by refdes (or model_path when
   * refdes is missing — same fallback the analyzer uses). */
  markers: Map<string, Mesh>;
  /** All materials + geometries the loader created — `dispose()`
   * walks this so callers don't leak GL resources on scene swap. */
  dispose(): void;
}

/**
 * Build the geometry described by `scene`. The returned group is
 * positioned with its centroid at world `(0, 0, 0)` so the viewer's
 * default camera frames the whole board without extra translation.
 */
export function loadThreeScene(
  scene: ThreeScene,
  theme: SceneTheme = DEFAULT_THEME,
): LoadedScene {
  const group = new Group();
  group.name = "kithree.scene";

  const resources: { geometries: BufferGeometry[]; materials: Material[] } = {
    geometries: [],
    materials: [],
  };
  const markers = new Map<string, Mesh>();

  // Compute the board centroid so we can centre everything on origin.
  const centroid = polygonCentroid(scene.board_outline_mm);

  // --- board ---------------------------------------------------------
  const boardMesh = buildBoardMesh(scene, theme, resources, centroid);
  if (boardMesh) group.add(boardMesh);

  // --- placement markers --------------------------------------------
  for (const placement of scene.placements) {
    const marker = buildPlacementMarker(placement, theme, resources, centroid, scene.board_thickness_mm);
    group.add(marker);
    markers.set(placement.refdes || placement.model_path, marker);
  }

  return {
    group,
    boardMesh,
    markers,
    dispose() {
      for (const g of resources.geometries) g.dispose();
      for (const m of resources.materials) m.dispose();
      resources.geometries.length = 0;
      resources.materials.length = 0;
      markers.clear();
    },
  };
}

function buildPlacementMarker(
  placement: ScenePlacement,
  theme: SceneTheme,
  resources: { geometries: BufferGeometry[]; materials: Material[] },
  centroid: { x: number; y: number },
  boardThicknessMm: number,
): Mesh {
  const [sx, sy, sz] = placement.scale;
  const length = DEFAULT_MARKER_LWH_MM.length * (sx || 1);
  const width = DEFAULT_MARKER_LWH_MM.width * (sy || 1);
  const height = DEFAULT_MARKER_LWH_MM.height * (sz || 1);
  const geom = new BoxGeometry(length, height, width);
  resources.geometries.push(geom);
  const mat = new MeshStandardMaterial({
    color: new Color(
      placement.side === "bottom" ? theme.bottomMarkerColor : theme.topMarkerColor,
    ),
    metalness: 0.3,
    roughness: 0.4,
  });
  resources.materials.push(mat);

  const mesh = new Mesh(geom, mat);
  mesh.name = `kithree.placement.${placement.refdes || placement.model_path}`;

  const [px, py, pz] = placement.position_mm;
  const xOffset = px - centroid.x;
  // The KiCad Y axis points down; three.js Y is up. We mapped board
  // outline points the same way (`-(y - centroid.y)`) so placements
  // need the same flip to stay aligned.
  const zOffset = -(py - centroid.y);
  // Board top sits at world Y = boardThicknessMm (we extruded along
  // +Z then rotated, so the top surface is at +Y after the rotation).
  const yTopSurface = boardThicknessMm + height / 2;
  const yBottomSurface = -height / 2;
  const baseY = placement.side === "bottom" ? yBottomSurface : yTopSurface;
  const modelDeltaY = pz; // model offset z stacks above the surface
  mesh.position.set(xOffset, baseY + (placement.side === "bottom" ? -modelDeltaY : modelDeltaY), zOffset);

  // KiCad rotation: (rx, ry, rz) in degrees, ZYX order in pcbnew's
  // renderer. Three.js Euler order defaults to "XYZ" — set "ZYX" so
  // composition matches the Rust side.
  const [rx, ry, rz] = placement.rotation_deg;
  mesh.rotation.order = "ZYX";
  mesh.rotation.x = degToRad(rx);
  mesh.rotation.y = degToRad(rz); // KiCad's board-plane rotation lands on three's Y
  mesh.rotation.z = degToRad(ry);

  // Flip bottom-side parts 180° around the in-plane (X) axis so the
  // marker sits below the board with the same orientation cue.
  if (placement.side === "bottom") {
    mesh.rotateX(Math.PI);
  }

  return mesh;
}

/** Build the extruded board mesh, or null when the scene has no outline.
 * Shared by both the box-marker and real-model loaders. */
function buildBoardMesh(
  scene: ThreeScene,
  theme: SceneTheme,
  resources: { geometries: BufferGeometry[]; materials: Material[] },
  centroid: { x: number; y: number },
): Mesh | null {
  if (scene.board_outline_mm.length < 3) return null;
  const shape = new Shape(
    scene.board_outline_mm.map(([x, y]) => new Vector2(x - centroid.x, -(y - centroid.y))),
  );
  const boardGeom = new ExtrudeGeometry(shape, {
    depth: Math.max(scene.board_thickness_mm, 0.01),
    bevelEnabled: false,
    curveSegments: 12,
  });
  // Three.js extrudes along +Z by default; rotate −π/2 about X so +Z becomes
  // the board's thickness axis and the XY plane is the board surface.
  boardGeom.rotateX(-Math.PI / 2);
  resources.geometries.push(boardGeom);
  const boardMat = new MeshStandardMaterial({
    color: new Color(theme.boardColor),
    metalness: 0.1,
    roughness: 0.7,
    side: DoubleSide,
  });
  resources.materials.push(boardMat);
  const boardMesh = new Mesh(boardGeom, boardMat);
  boardMesh.name = "kithree.board";
  return boardMesh;
}

/** Resolves a placement's `model_path` to STEP bytes (e.g. via the kiserver
 * `model3d` route). Return `null` to fall back to a placement box. */
export interface ModelFetcher {
  (modelPath: string): Promise<Uint8Array | null>;
}

export interface LoadSceneOptions {
  theme?: SceneTheme;
  /** When set, real STEP geometry is fetched + tessellated per placement. */
  fetchModel?: ModelFetcher;
}

/** Body colour for a component whose STEP carried no colour of its own. */
const DEFAULT_MODEL_COLOR = 0x2b2b2b;

/**
 * Like {@link loadThreeScene}, but renders each placement's real STEP geometry
 * when `opts.fetchModel` can supply the bytes — falling back to the
 * colour-coded placement box when a model is missing or fails to decode (the
 * common offline case). Each distinct `model_path` is fetched + tessellated
 * once and shared across every instance that references it.
 */
export async function loadThreeSceneWithModels(
  scene: ThreeScene,
  opts: LoadSceneOptions = {},
): Promise<LoadedScene> {
  const theme = opts.theme ?? DEFAULT_THEME;
  const group = new Group();
  group.name = "kithree.scene";
  const resources: { geometries: BufferGeometry[]; materials: Material[] } = {
    geometries: [],
    materials: [],
  };
  const markers = new Map<string, Mesh>();
  const centroid = polygonCentroid(scene.board_outline_mm);

  const boardMesh = buildBoardMesh(scene, theme, resources, centroid);
  if (boardMesh) group.add(boardMesh);

  // Decode each distinct model once; many placements share a model_path.
  const geomCache = new Map<string, BufferGeometry | null>();

  for (const placement of scene.placements) {
    let mesh: Mesh | null = null;
    if (opts.fetchModel && placement.model_path) {
      const geom = await resolveModelGeometry(
        placement.model_path,
        opts.fetchModel,
        geomCache,
        resources,
      );
      if (geom) {
        mesh = buildPlacementModel(placement, geom, resources, centroid, scene.board_thickness_mm);
      }
    }
    if (mesh === null) {
      mesh = buildPlacementMarker(placement, theme, resources, centroid, scene.board_thickness_mm);
    }
    group.add(mesh);
    markers.set(placement.refdes || placement.model_path, mesh);
  }

  return {
    group,
    boardMesh,
    markers,
    dispose() {
      for (const g of resources.geometries) g.dispose();
      for (const m of resources.materials) m.dispose();
      resources.geometries.length = 0;
      resources.materials.length = 0;
      markers.clear();
    },
  };
}

/** Fetch + tessellate a model once, caching by path (including a cached
 * `null` for the unavailable/undecodable case so the box fallback is sticky).
 * Pushes a successfully-built geometry into `resources` for disposal. */
async function resolveModelGeometry(
  modelPath: string,
  fetchModel: ModelFetcher,
  cache: Map<string, BufferGeometry | null>,
  resources: { geometries: BufferGeometry[]; materials: Material[] },
): Promise<BufferGeometry | null> {
  const cached = cache.get(modelPath);
  if (cached !== undefined) return cached;
  let geom: BufferGeometry | null = null;
  try {
    const bytes = await fetchModel(modelPath);
    if (bytes && bytes.length > 0) {
      geom = mergeStepMeshes(await decodeStep(bytes));
      resources.geometries.push(geom);
    }
  } catch {
    geom = null;
  }
  cache.set(modelPath, geom);
  return geom;
}

/** Pose a real tessellated model on the board: the model's Z-up frame is
 * rotated to the board's Y-up frame, then the placement's in-plane offset and
 * KiCad (rx, ry, rz) rotation + bottom flip are applied — mirroring the box
 * marker's coordinate handling. Exact KiCad-faithful seating is M4 polish. */
function buildPlacementModel(
  placement: ScenePlacement,
  geometry: BufferGeometry,
  resources: { geometries: BufferGeometry[]; materials: Material[] },
  centroid: { x: number; y: number },
  boardThicknessMm: number,
): Mesh {
  const mat = new MeshStandardMaterial({
    color: new Color(DEFAULT_MODEL_COLOR),
    metalness: 0.4,
    roughness: 0.5,
  });
  resources.materials.push(mat);
  const mesh = new Mesh(geometry, mat);
  mesh.name = `kithree.model.${placement.refdes || placement.model_path}`;

  const [px, py, pz] = placement.position_mm;
  const xOffset = px - centroid.x;
  const zOffset = -(py - centroid.y); // KiCad Y-down → three Z
  const surfaceY = placement.side === "bottom" ? 0 : boardThicknessMm;
  mesh.position.set(
    xOffset,
    surfaceY + (placement.side === "bottom" ? -pz : pz),
    zOffset,
  );

  // KiCad models are authored Z-up in mm; the board is Y-up after its extrude
  // rotation. Bring the model to Y-up with −90° about X, then compose the
  // KiCad model rotation (rx, ry, rz) the same way the box marker does.
  const [rx, ry, rz] = placement.rotation_deg;
  mesh.rotation.order = "ZYX";
  mesh.rotation.x = -Math.PI / 2 + degToRad(rx);
  mesh.rotation.y = degToRad(rz);
  mesh.rotation.z = degToRad(ry);
  if (placement.side === "bottom") {
    mesh.rotateX(Math.PI);
  }
  return mesh;
}

function polygonCentroid(points: Array<[number, number]>): { x: number; y: number } {
  if (points.length === 0) return { x: 0, y: 0 };
  let sx = 0;
  let sy = 0;
  for (const [x, y] of points) {
    sx += x;
    sy += y;
  }
  return { x: sx / points.length, y: sy / points.length };
}

function degToRad(deg: number): number {
  return (deg * Math.PI) / 180;
}
