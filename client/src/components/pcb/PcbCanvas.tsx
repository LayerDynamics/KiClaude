import { useEffect, useRef, useState } from "react";

import {
  loadKicanvas,
  type KicanvasReady,
} from "../../lib/kicanvas-bridge";
import { Card } from "../UI";

export interface PcbCanvasProps {
  /** URL to a `.kicad_pcb` (or `.kicad_pro`). */
  src: string;
  /** Pan/zoom + overlay UI level. Defaults to `"full"`. */
  controls?: "none" | "basic" | "full";
  /** Optional list of `controlslist` flags (e.g. `"nodownload nooverlay"`). */
  controlslist?: string;
  /** Friendly name shown in the kicanvas overlay. */
  name?: string;
  /** Test seam: override the kicanvas loader (default is real bridge). */
  loader?: (
    opts?: Parameters<typeof loadKicanvas>[0],
  ) => Promise<KicanvasReady>;
  /** Fixed pixel height for the viewport. Defaults to `480`. */
  height?: number;
  /** Optional className for the outer wrapper. */
  className?: string;
}

type Status = "loading" | "ready" | "error";

/**
 * Embed a kicanvas WebGL board view for a single `.kicad_pcb` (or
 * `.kicad_pro`) URL. The wrapper takes care of:
 *
 * - Lazy-loading the vendored kicanvas bundle exactly once across the
 *   app, via {@link loadKicanvas} from `lib/kicanvas-bridge`.
 * - Re-running the embed when `src` changes (kicanvas itself doesn't
 *   react to `src` attribute mutations cleanly, so we remount).
 * - Surfacing a Playwright-friendly `data-testid="pcb-canvas"` and
 *   a `data-status="ready"` attribute once the board is rendered.
 *
 * The container is given a fixed `height` so the canvas has room to
 * draw; kicanvas fills its parent box and listens to its own
 * `ResizeObserver` internally for layout changes.
 */
export function PcbCanvas(props: PcbCanvasProps) {
  const {
    src,
    controls = "full",
    controlslist,
    name,
    loader,
    height = 480,
    className,
  } = props;

  const [status, setStatus] = useState<Status>("loading");
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = loader ?? loadKicanvas;

    setStatus("loading");
    setError(null);

    (async () => {
      try {
        await load();
        if (cancelled) return;
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [loader, src]);

  if (status === "error") {
    return (
      <Card
        tone="danger"
        flush
        data-testid="pcb-canvas"
        data-status="error"
        role="alert"
        className={className}
      >
        <div className="p-3 text-sm text-red-700 dark:text-red-300">
          kicanvas failed to load: {error}
        </div>
      </Card>
    );
  }

  if (status === "loading") {
    return (
      <Card
        tone="muted"
        flush
        data-testid="pcb-canvas"
        data-status="loading"
        className={className}
      >
        <div
          className="flex items-center justify-center text-sm text-[var(--text)]/70"
          style={{ height }}
        >
          loading kicanvas…
        </div>
      </Card>
    );
  }

  return (
    <div
      data-testid="pcb-canvas"
      data-status="ready"
      ref={containerRef}
      style={{ height, width: "100%", position: "relative" }}
      className={className}
    >
      <kicanvas-embed
        // `key` forces a remount when the underlying src changes,
        // sidestepping the upstream limitation that kicanvas-embed
        // does not observe its own `src` attribute mutations.
        key={src}
        controls={controls}
        controlslist={controlslist}
        data-testid="kicanvas-embed"
        style={{ display: "block", width: "100%", height: "100%" }}
      >
        <kicanvas-source
          src={src}
          {...(name ? { name } : {})}
          data-testid="kicanvas-source"
        />
      </kicanvas-embed>
    </div>
  );
}
