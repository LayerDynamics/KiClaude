/**
 * LibraryImport — FR-043 drag-drop import of a `.kicad_sym` / `.kicad_mod`
 * onto the editor.
 *
 * `useLibraryImport` reads the dropped file, infers the kind from its
 * extension, and POSTs it to the gateway
 * (`/api/server/project/{id}/library/import`), which writes it into a
 * project-local library and registers a lib-table row. `LibraryImportDropZone`
 * is a thin drop target that drives the hook and surfaces the result.
 */

import { useCallback, useState } from "react";

export interface ImportResult {
  ok: boolean;
  kind: string;
  nickname: string;
  lib_id_prefix: string;
  uri: string;
}

type ImportState = "idle" | "importing" | "done" | "error";

function kindForFile(name: string): "symbol" | "footprint" | null {
  if (name.endsWith(".kicad_sym")) return "symbol";
  if (name.endsWith(".kicad_mod")) return "footprint";
  return null;
}

export interface UseLibraryImportOptions {
  apiBase?: string;
  fetcher?: typeof fetch;
}

export function useLibraryImport(projectId: string, opts: UseLibraryImportOptions = {}) {
  const { apiBase = "/api/server", fetcher } = opts;
  const [state, setState] = useState<ImportState>("idle");
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const importFile = useCallback(
    async (file: File): Promise<ImportResult | null> => {
      const kind = kindForFile(file.name);
      if (!kind) {
        setState("error");
        setError(`unsupported file ${file.name} — drop a .kicad_sym or .kicad_mod`);
        return null;
      }
      setState("importing");
      setError(null);
      try {
        const content = await file.text();
        const doFetch = fetcher ?? globalThis.fetch;
        const resp = await doFetch(
          `${apiBase}/project/${encodeURIComponent(projectId)}/library/import`,
          {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ filename: file.name, content, kind }),
          },
        );
        if (!resp.ok) {
          throw new Error(`import failed (HTTP ${resp.status})`);
        }
        const body = (await resp.json()) as ImportResult;
        setResult(body);
        setState("done");
        return body;
      } catch (err: unknown) {
        setState("error");
        setError(err instanceof Error ? err.message : "import failed");
        return null;
      }
    },
    [projectId, apiBase, fetcher],
  );

  return { state, result, error, importFile };
}

export interface LibraryImportDropZoneProps extends UseLibraryImportOptions {
  projectId: string;
  onImported?: (result: ImportResult) => void;
  children?: React.ReactNode;
}

export function LibraryImportDropZone(props: LibraryImportDropZoneProps): React.JSX.Element {
  const { projectId, apiBase, fetcher, onImported, children } = props;
  const { state, result, error, importFile } = useLibraryImport(projectId, { apiBase, fetcher });

  const onDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      const file = e.dataTransfer?.files?.[0];
      if (!file) return;
      const imported = await importFile(file);
      if (imported && onImported) onImported(imported);
    },
    [importFile, onImported],
  );

  return (
    <div
      data-testid="library-import-dropzone"
      data-state={state}
      onDragOver={(e) => e.preventDefault()}
      onDrop={onDrop}
    >
      {children}
      {state === "done" && result ? (
        <span data-testid="import-done">
          Imported {result.nickname} ({result.lib_id_prefix})
        </span>
      ) : null}
      {state === "error" && error ? (
        <span role="alert" data-testid="import-error">
          {error}
        </span>
      ) : null}
    </div>
  );
}
