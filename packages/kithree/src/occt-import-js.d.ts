/**
 * Minimal ambient declaration for `occt-import-js` (no types published).
 * Covers only the slice kithree's STEP decoder uses: the default factory
 * returns a module exposing `ReadStepFile`, which yields meshes with
 * position/normal attribute arrays + a triangle index array.
 */
declare module "occt-import-js" {
  interface OcctMeshAttribute {
    array: number[];
  }
  interface OcctMesh {
    name?: string;
    color?: [number, number, number];
    attributes: {
      position: OcctMeshAttribute;
      normal?: OcctMeshAttribute;
    };
    index: { array: number[] };
  }
  export interface OcctReadResult {
    success: boolean;
    meshes: OcctMesh[];
  }
  export interface OcctModule {
    ReadStepFile(content: Uint8Array, params: unknown): OcctReadResult;
  }
  /** Optional Emscripten knobs (e.g. `locateFile` to point at the .wasm). */
  interface OcctInitOptions {
    locateFile?: (path: string, prefix: string) => string;
  }
  type OcctFactory = (options?: OcctInitOptions) => Promise<OcctModule>;
  const factory: OcctFactory;
  export default factory;
}
