// @vitest-environment node
//
// occt-import-js loads its OCCT wasm through Node's fs, so this suite runs in
// the node environment (not happy-dom). It exercises the real STEP decoder
// against a real KiCad model from the bundled mirror — the T10 empirical gate.

import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { Mesh } from "three";
import { describe, expect, it } from "vitest";

import { loadThreeSceneWithModels, type ScenePlacement, type ThreeScene } from "./scene.js";
import { decodeStep, mergeStepMeshes } from "./step.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "../../..");
const STEP_PATH = resolve(
  REPO_ROOT,
  "libs/packages3D/Capacitor_SMD.3dshapes/C_0402_1005Metric.step",
);
const HAS_MODEL = existsSync(STEP_PATH);

function stepBytes(): Uint8Array {
  return new Uint8Array(readFileSync(STEP_PATH));
}

const C1: ScenePlacement = {
  refdes: "C1",
  model_path: "${KICAD9_3DMODEL_DIR}/Capacitor_SMD.3dshapes/C_0402_1005Metric.wrl",
  position_mm: [10, 10, 0],
  scale: [1, 1, 1],
  rotation_deg: [0, 0, 0],
  side: "top",
};

const SCENE: ThreeScene = {
  board_thickness_mm: 1.6,
  board_outline_mm: [
    [0, 0],
    [20, 0],
    [20, 20],
    [0, 20],
  ],
  placements: [C1],
};

describe.runIf(HAS_MODEL)("decodeStep (T10)", () => {
  it("tessellates a real KiCad .step into non-trivial geometry", async () => {
    const meshes = await decodeStep(stepBytes());
    expect(meshes.length).toBeGreaterThan(0);
    const verts = meshes.reduce((n, m) => n + m.positions.length / 3, 0);
    const tris = meshes.reduce((n, m) => n + m.indices.length / 3, 0);
    expect(verts).toBeGreaterThan(0);
    expect(tris).toBeGreaterThan(0);
  });

  it("merges solids into one indexed BufferGeometry", async () => {
    const geom = mergeStepMeshes(await decodeStep(stepBytes()));
    expect(geom.getAttribute("position").count).toBeGreaterThan(0);
    const index = geom.getIndex();
    expect(index).not.toBeNull();
    expect((index?.count ?? 0) % 3).toBe(0);
  });

  it("rejects bytes that are not a STEP file", async () => {
    await expect(decodeStep(new Uint8Array([1, 2, 3, 4]))).rejects.toThrow();
  });
});

describe.runIf(HAS_MODEL)("loadThreeSceneWithModels (T10)", () => {
  it("renders real model geometry when the fetcher supplies STEP bytes", async () => {
    const loaded = await loadThreeSceneWithModels(SCENE, {
      fetchModel: async () => stepBytes(),
    });
    const mesh = loaded.markers.get("C1") as Mesh;
    expect(mesh.name).toBe("kithree.model.C1");
    expect(mesh.geometry.getAttribute("position").count).toBeGreaterThan(0);
    loaded.dispose();
  });

  it("falls back to a placement box when the model is unavailable", async () => {
    const loaded = await loadThreeSceneWithModels(SCENE, {
      fetchModel: async () => null,
    });
    const mesh = loaded.markers.get("C1") as Mesh;
    expect(mesh.name).toBe("kithree.placement.C1");
    loaded.dispose();
  });

  it("tessellates a shared model once across repeated placements", async () => {
    let calls = 0;
    const scene: ThreeScene = {
      ...SCENE,
      placements: [C1, { ...C1, refdes: "C2", position_mm: [5, 5, 0] }],
    };
    const loaded = await loadThreeSceneWithModels(scene, {
      fetchModel: async () => {
        calls += 1;
        return stepBytes();
      },
    });
    expect(calls).toBe(1); // identical model_path → fetched + tessellated once
    const c1 = loaded.markers.get("C1") as Mesh;
    const c2 = loaded.markers.get("C2") as Mesh;
    expect(c1.geometry).toBe(c2.geometry); // shared geometry instance
    loaded.dispose();
  });
});
