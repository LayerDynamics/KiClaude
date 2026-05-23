import { useEffect, useState } from "react";

import { Card } from "./UI";
import { loadKiclaudeWasm } from "../lib/wasm";

/** A minimal blinky-style `.kicad_pro` JSON — embedded so the debug
 * pane can demonstrate the wasm pipeline without going through the
 * filesystem. The real "open a project from disk" flow lands in
 * M0-T-04 (kicanvas) using the File System Access API. */
const DEMO_PRO = JSON.stringify({
  meta: { filename: "blinky.kicad_pro", generator: "kiclaude" },
  text_variables: { BOARD: "blinky" },
});

const DEMO_PCB = `(kicad_pcb (version 20240108) (generator kiclaude)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
  (setup (pad_to_mask_clearance 0.0))
  (net 0 ""))
`;

export interface DebugPaneProps {
  /** Optional override for the wasm loader — tests inject a stub. */
  loader?: () => Promise<{
    ki: { openProjectFromStrings: (pro: string, pcb: string, fallback: string) => unknown };
  }>;
  /** Optional override for the demo .kicad_pro text. */
  pro?: string;
  /** Optional override for the demo .kicad_pcb text. */
  pcb?: string;
}

/**
 * Tiny developer pane: load the kiclaude wasm bindings, call
 * `openProjectFromStrings` on a demo blinky project, render the
 * resulting `kcir::Project` JSON. Used as the M0-T-02 acceptance
 * surface for the wasm bootstrap.
 */
export function DebugPane(props: DebugPaneProps = {}) {
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [result, setResult] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const loader = props.loader ?? loadKiclaudeWasm;
    (async () => {
      try {
        const bindings = await loader();
        const json = bindings.ki.openProjectFromStrings(
          props.pro ?? DEMO_PRO,
          props.pcb ?? DEMO_PCB,
          "blinky",
        );
        if (cancelled) return;
        setResult(json);
        setStatus("ready");
        // Tag globalThis for Playwright (M0-Q-03) to wait on.
        (globalThis as Record<string, unknown>).__kiclaude_wasm_ready = true;
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.loader, props.pro, props.pcb]);

  if (status === "error") {
    return (
      <Card tone="danger" flush>
        <pre
          data-testid="debug-pane-error"
          className="m-0 whitespace-pre-wrap p-3 font-mono text-sm text-red-700 dark:text-red-300"
        >
          wasm error: {error}
        </pre>
      </Card>
    );
  }
  if (status === "loading") {
    return (
      <Card tone="muted" flush>
        <pre
          data-testid="debug-pane-loading"
          className="m-0 p-3 font-mono text-sm text-[var(--text)]"
        >
          loading kiclaude wasm…
        </pre>
      </Card>
    );
  }
  return (
    <Card tone="muted" flush>
      <pre
        data-testid="debug-pane-result"
        className="m-0 overflow-auto p-3 text-left font-mono text-xs leading-snug text-[var(--text-h)]"
      >
        {JSON.stringify(result, null, 2)}
      </pre>
    </Card>
  );
}
