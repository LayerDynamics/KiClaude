/**
 * Bridge between React and the kicanvas custom elements.
 *
 * kicanvas (https://github.com/theacodes/kicanvas) is shipped as a
 * self-contained ES module bundle that registers two custom elements
 * (`<kicanvas-embed>` and `<kicanvas-source>`) on the global
 * `customElements` registry. It is not on npm; we pin a SHA in
 * `client/package.json` and build the bundle into
 * `client/public/vendor/kicanvas.js` via `scripts/build-kicanvas.mjs`.
 *
 * This module owns the one-time script-tag injection so multiple
 * `PcbCanvas` instances don't race to register the elements twice.
 */

/** URL of the vendored kicanvas bundle. Default is the postinstall
 * artifact path served by Vite from `client/public/`. */
export const KICANVAS_SCRIPT_URL = "/vendor/kicanvas.js";

const SCRIPT_DATA_ID = "kiclaude-kicanvas-script";
const CUSTOM_ELEMENT_NAMES = ["kicanvas-embed", "kicanvas-source"] as const;

export type KicanvasElementName = (typeof CUSTOM_ELEMENT_NAMES)[number];

export interface KicanvasLoadOptions {
  /** Override the bundle URL; useful in tests. */
  scriptUrl?: string;
  /** Override the target document; useful in tests / SSR shims. */
  document?: Document;
  /** Max wait (ms) for the custom elements to register after the
   * `<script>` tag's `load` event fires. Defaults to 5000. */
  registrationTimeoutMs?: number;
}

/** Result of {@link loadKicanvas}. Returned in two cases: the bundle
 * was already registered, or this call performed the injection and
 * the custom elements are now ready to render. */
export interface KicanvasReady {
  status: "ready";
  /** Tells callers whether this load was a no-op cache hit. */
  cached: boolean;
}

let loadPromise: Promise<KicanvasReady> | null = null;

/**
 * Inject the kicanvas module script (idempotent) and resolve once both
 * custom elements are registered. Safe to call from multiple components
 * concurrently — the promise is memoised.
 */
export function loadKicanvas(opts: KicanvasLoadOptions = {}): Promise<KicanvasReady> {
  if (loadPromise) return loadPromise;

  const inflight: Promise<KicanvasReady> = (async (): Promise<KicanvasReady> => {
    const doc = opts.document ?? globalThis.document;
    if (!doc) {
      throw new Error(
        "loadKicanvas: no `document` available — call this from a browser context.",
      );
    }
    const win = doc.defaultView as (Window & typeof globalThis) | null;
    const customElements = win?.customElements ?? globalThis.customElements;
    if (!customElements) {
      throw new Error("loadKicanvas: `customElements` registry not available.");
    }

    if (CUSTOM_ELEMENT_NAMES.every((name) => customElements.get(name) !== undefined)) {
      return { status: "ready", cached: true };
    }

    const scriptUrl = opts.scriptUrl ?? KICANVAS_SCRIPT_URL;
    let script = doc.getElementById(SCRIPT_DATA_ID) as HTMLScriptElement | null;
    if (!script) {
      script = doc.createElement("script");
      script.id = SCRIPT_DATA_ID;
      script.type = "module";
      script.src = scriptUrl;
      script.async = false;
      doc.head.appendChild(script);
    }

    await waitForScript(script);

    const timeoutMs = opts.registrationTimeoutMs ?? 5_000;
    await waitForRegistration(customElements, timeoutMs);
    return { status: "ready", cached: false };
  })().catch((err: unknown) => {
    loadPromise = null; // allow retry after failure
    throw err;
  });

  loadPromise = inflight;
  return inflight;
}

/** Test-only: forget any cached load promise. */
export function resetKicanvasLoaderForTests(): void {
  loadPromise = null;
}

function waitForScript(script: HTMLScriptElement): Promise<void> {
  // Module scripts mark `readyState` as undefined; presence of
  // `dataset.loaded` is our own breadcrumb in case the listener missed.
  if (script.dataset.loaded === "true") return Promise.resolve();
  return new Promise<void>((resolveScript, rejectScript) => {
    const onLoad = (): void => {
      script.dataset.loaded = "true";
      cleanup();
      resolveScript();
    };
    const onError = (ev: Event | string): void => {
      cleanup();
      const msg = typeof ev === "string" ? ev : "kicanvas bundle failed to load";
      rejectScript(new Error(msg));
    };
    const cleanup = (): void => {
      script.removeEventListener("load", onLoad);
      script.removeEventListener("error", onError);
    };
    script.addEventListener("load", onLoad, { once: true });
    script.addEventListener("error", onError, { once: true });
  });
}

async function waitForRegistration(
  customElements: CustomElementRegistry,
  timeoutMs: number,
): Promise<void> {
  const pending = CUSTOM_ELEMENT_NAMES.filter(
    (name) => customElements.get(name) === undefined,
  );
  if (pending.length === 0) return;
  const whenDefined = Promise.all(pending.map((name) => customElements.whenDefined(name)));
  let timer: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      reject(
        new Error(
          `kicanvas custom elements did not register within ${timeoutMs}ms (waiting on: ${pending.join(", ")})`,
        ),
      );
    }, timeoutMs);
  });
  try {
    await Promise.race([whenDefined, timeout]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * JSX intrinsic-element typings for the kicanvas custom elements.
 * Mirrors the documented attributes from https://kicanvas.org/embedding.
 */
export interface KicanvasEmbedAttributes
  extends React.HTMLAttributes<HTMLElement> {
  /** Direct URL to a `.kicad_pcb`, `.kicad_sch`, or `.kicad_pro`. */
  src?: string;
  /** `"none"` | `"basic"` | `"full"` — pan/zoom + overlay UI. */
  controls?: "none" | "basic" | "full";
  /** Space-separated control flags (`nodownload`, `nooverlay`, `noflipview`). */
  controlslist?: string;
  /** Theme name; documented but not fully implemented upstream. */
  theme?: string;
  /** Initial zoom; documented but not fully implemented upstream. */
  zoom?: string;
}

export interface KicanvasSourceAttributes
  extends React.HTMLAttributes<HTMLElement> {
  /** Source file URL. Mutually exclusive with `textContent`. */
  src?: string;
  /** Optional file type hint. */
  type?: "schematic" | "board" | "project" | "worksheet";
  /** Friendly name (shown in the overlay). */
  name?: string;
}

declare module "react" {
  namespace JSX {
    interface IntrinsicElements {
      "kicanvas-embed": React.DetailedHTMLProps<KicanvasEmbedAttributes, HTMLElement>;
      "kicanvas-source": React.DetailedHTMLProps<KicanvasSourceAttributes, HTMLElement>;
    }
  }
}
