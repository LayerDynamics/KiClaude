/**
 * SharePage — FR-080 read-only shared project view.
 *
 * Renders a frozen, content-addressed project snapshot reached via
 * `#/share/<token>`. The token is the snapshot's manifest content key
 * (immutable + tamper-evident), so this page is strictly read-only: it
 * fetches the share metadata from the gateway (`/api/server/share/...`),
 * lists the frozen files with direct download links, and mounts a
 * read-only kicanvas board preview loaded straight from the share's
 * `.kicad_pcb` file endpoint. There is no edit overlay, no chat, and no
 * mutation surface — viewing a share never changes anything.
 */

import { useEffect, useRef, useState } from "react";

import { loadKicanvas } from "../lib/kicanvas-bridge";

export interface ShareMeta {
  ok: boolean;
  read_only: boolean;
  token: string;
  project_name: string;
  created_at: string;
  files: string[];
}

export interface SharePageProps {
  /** Share token from `#/share/<token>`; `null` when the hash has none. */
  token: string | null;
  /** Gateway base — defaults to `/api/server` (proxies to kiserver). */
  apiBase?: string;
  /** Injectable fetch for tests; defaults to the global `fetch`. */
  fetcher?: typeof fetch;
}

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; meta: ShareMeta };

function pcbPath(files: string[]): string | null {
  return files.find((f) => f.endsWith(".kicad_pcb")) ?? null;
}

export function SharePage(props: SharePageProps): React.JSX.Element {
  const { token, apiBase = "/api/server", fetcher } = props;
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const embedRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!token) {
      setState({ status: "error", message: "No share token in the URL." });
      return;
    }
    setState({ status: "loading" });
    const doFetch = fetcher ?? globalThis.fetch;
    doFetch(`${apiBase}/share/${encodeURIComponent(token)}`)
      .then(async (resp) => {
        if (!resp.ok) {
          throw new Error(
            resp.status === 404
              ? "This share link was not found (it may have expired or never existed)."
              : `Failed to load share (HTTP ${resp.status}).`,
          );
        }
        return (await resp.json()) as ShareMeta;
      })
      .then((meta) => {
        if (!cancelled) setState({ status: "ready", meta });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setState({
            status: "error",
            message: err instanceof Error ? err.message : "Failed to load share.",
          });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [token, apiBase, fetcher]);

  // Best-effort: register the kicanvas custom elements so the read-only
  // board preview renders. Failure (e.g. bundle missing in a test DOM)
  // leaves the rest of the page fully functional.
  useEffect(() => {
    if (state.status !== "ready") return;
    void loadKicanvas().catch(() => {
      /* preview unavailable; metadata + downloads still work */
    });
  }, [state.status]);

  if (state.status === "loading") {
    return (
      <main className="share-page" data-testid="share-loading">
        <p>Loading shared project…</p>
      </main>
    );
  }

  if (state.status === "error") {
    return (
      <main className="share-page" data-testid="share-error">
        <h1>Shared project</h1>
        <p role="alert" className="share-error">
          {state.message}
        </p>
      </main>
    );
  }

  const { meta } = state;
  const fileUrl = (rel: string) =>
    `${apiBase}/share/${encodeURIComponent(meta.token)}/file?path=${encodeURIComponent(rel)}`;
  const boardPath = pcbPath(meta.files);

  return (
    <main className="share-page" data-testid="share-ready">
      <header className="share-header">
        <span className="share-badge" data-testid="share-readonly-badge">
          Read-only shared snapshot
        </span>
        <h1>{meta.project_name || "Shared project"}</h1>
        {meta.created_at ? (
          <p className="share-meta" data-testid="share-created-at">
            Shared {meta.created_at}
          </p>
        ) : null}
      </header>

      {boardPath ? (
        <section className="share-board" data-testid="share-board">
          {/* Read-only: basic controls, no download/overlay edit surface. */}
          <kicanvas-embed
            ref={embedRef as React.Ref<HTMLElement>}
            controls="basic"
            controlslist="nodownload nooverlay"
            data-testid="share-kicanvas-embed"
          >
            <kicanvas-source src={fileUrl(boardPath)} data-testid="share-kicanvas-source" />
          </kicanvas-embed>
        </section>
      ) : null}

      <section className="share-files">
        <h2>Files in this snapshot</h2>
        <ul data-testid="share-file-list">
          {meta.files.map((rel) => (
            <li key={rel}>
              <a href={fileUrl(rel)} download>
                {rel}
              </a>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
