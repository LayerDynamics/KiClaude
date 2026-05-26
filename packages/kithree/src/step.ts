/**
 * STEP → mesh decoder (T10 / FR-029 / SPEC §6.6).
 *
 * Tessellates a `.step` component model (the kind shipped in the bundled
 * `kicad-packages3D` mirror) into renderable three.js buffers via
 * `occt-import-js` — OpenCASCADE compiled to wasm, the same kernel KiCad and
 * online-3d-viewer use, so it handles the trimmed-NURBS B-rep that real
 * KiCad models carry. The wasm module is lazily imported and instantiated
 * once, then reused across every decode.
 */

import { BufferAttribute, BufferGeometry } from "three";
import occtimportjs, { type OcctModule } from "occt-import-js";

/** One tessellated solid from a STEP file, in the file's native units (mm). */
export interface StepMesh {
  name: string;
  /** xyz triples. */
  positions: Float32Array;
  /** xyz triples, or null when the kernel emitted none (we recompute). */
  normals: Float32Array | null;
  /** Triangle vertex indices. */
  indices: Uint32Array;
  /** 0..1 linear RGB if the STEP carried a colour, else null. */
  color: [number, number, number] | null;
}

let _occt: Promise<OcctModule> | null = null;

/** Lazily import + instantiate the OCCT wasm module (once per process). The
 * dynamic import keeps the ~7 MB wasm out of the initial bundle until a board
 * actually needs a 3D model. */
async function occt(): Promise<OcctModule> {
  if (_occt === null) {
    _occt = occtimportjs();
  }
  return _occt;
}

/**
 * Decode STEP bytes into one or more meshes. Throws when OCCT reports failure
 * or the file yields no geometry — callers fall back to a placement box.
 */
export async function decodeStep(bytes: Uint8Array): Promise<StepMesh[]> {
  const module = await occt();
  const result = module.ReadStepFile(bytes, null);
  if (!result.success || result.meshes.length === 0) {
    throw new Error("occt-import-js produced no geometry from STEP input");
  }
  return result.meshes.map((m) => ({
    name: m.name ?? "",
    positions: new Float32Array(m.attributes.position.array),
    normals: m.attributes.normal ? new Float32Array(m.attributes.normal.array) : null,
    indices: new Uint32Array(m.index.array),
    color: m.color ?? null,
  }));
}

/** Build a three.js `BufferGeometry` from a decoded {@link StepMesh}. Computes
 * vertex normals when OCCT didn't supply them so lighting still works. */
export function stepMeshToGeometry(mesh: StepMesh): BufferGeometry {
  const geom = new BufferGeometry();
  geom.setAttribute("position", new BufferAttribute(mesh.positions, 3));
  geom.setIndex(new BufferAttribute(mesh.indices, 1));
  if (mesh.normals) {
    geom.setAttribute("normal", new BufferAttribute(mesh.normals, 3));
  } else {
    geom.computeVertexNormals();
  }
  return geom;
}

/** Merge the solids of one STEP file into a single `BufferGeometry` so every
 * placement of that model shares one geometry. A multi-solid part (e.g. a
 * connector body + pins) becomes one mesh; per-solid colour is dropped in
 * favour of a single body material. */
export function mergeStepMeshes(meshes: StepMesh[]): BufferGeometry {
  if (meshes.length === 0) {
    throw new Error("cannot merge an empty mesh list");
  }
  let vertexFloats = 0;
  let indexCount = 0;
  for (const m of meshes) {
    vertexFloats += m.positions.length;
    indexCount += m.indices.length;
  }
  const positions = new Float32Array(vertexFloats);
  const normals = new Float32Array(vertexFloats);
  const indices = new Uint32Array(indexCount);
  let floatOffset = 0;
  let indexOffset = 0;
  let baseVertex = 0;
  let anyMissingNormals = false;
  for (const m of meshes) {
    positions.set(m.positions, floatOffset);
    if (m.normals) {
      normals.set(m.normals, floatOffset);
    } else {
      anyMissingNormals = true;
    }
    // Shift this solid's indices past the vertices already written.
    indices.set(
      m.indices.map((i) => i + baseVertex),
      indexOffset,
    );
    floatOffset += m.positions.length;
    indexOffset += m.indices.length;
    baseVertex += m.positions.length / 3;
  }
  const geom = new BufferGeometry();
  geom.setAttribute("position", new BufferAttribute(positions, 3));
  geom.setIndex(new BufferAttribute(indices, 1));
  if (anyMissingNormals) {
    geom.computeVertexNormals();
  } else {
    geom.setAttribute("normal", new BufferAttribute(normals, 3));
  }
  return geom;
}
