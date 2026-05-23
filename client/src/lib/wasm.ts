/**
 * Async loader for the kiclaude wasm packages (`kiclaude-ki` and
 * `kiclaude-cad`, both built by `wasm-pack build --target web crates/*`).
 *
 * The wasm-pack output for `--target web` ships an explicit init
 * function as the default export; you must call it once before any
 * bound function works. {@link loadKiclaudeWasm} memoizes that
 * one-time init so callers can use the returned bindings directly.
 *
 * The `?init` import suffix tells Vite (via `vite-plugin-wasm`) to
 * treat the wasm file as a fetchable asset rather than an ESM module
 * — without that suffix Vite would try to inline the wasm binary.
 */

import init, * as kiBindings from "kiclaude-ki";
import initCad, * as cadBindings from "kiclaude-cad";

let loadPromise: Promise<KiclaudeWasm> | null = null;

export interface KiclaudeWasm {
  ki: typeof kiBindings;
  cad: typeof cadBindings;
}

export async function loadKiclaudeWasm(): Promise<KiclaudeWasm> {
  if (loadPromise) return loadPromise;
  loadPromise = (async () => {
    await Promise.all([init(), initCad()]);
    return { ki: kiBindings, cad: cadBindings };
  })();
  return loadPromise;
}

/** Reset the cached init promise. Test-only — production code should
 * never call this. Exposed for unit tests that need to assert init is
 * called exactly once. */
export function _resetWasmLoaderForTests(): void {
  loadPromise = null;
}
