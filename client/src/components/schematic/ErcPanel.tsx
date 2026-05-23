import { useCallback, useMemo, useState } from "react";

import { Panel, Text } from "../UI";

export type ErcSeverity = "error" | "warning" | "exclusion" | "ignore";

export interface ErcIssue {
  severity: ErcSeverity;
  sheet: string;
  position_mm: [number, number];
  type: string;
  description: string;
}

export interface ErcReport {
  ok: boolean;
  issues: ErcIssue[];
  error?: string | null;
  duration_ms?: number;
  exit_code?: number | null;
}

export interface ErcPanelProps {
  projectId: string;
  projectPath: string;
  /** Gateway base URL. Defaults to `/api/connector`. */
  apiBase?: string;
  /** Test seam. */
  fetcher?: typeof fetch;
  /** Camera fly-to handler — fires when the user clicks an issue. */
  onFlyTo?: (sheet_uuid: string, position_mm: [number, number]) => void;
}

const SEVERITY_ORDER: ErcSeverity[] = [
  "error",
  "warning",
  "exclusion",
  "ignore",
];

const SEVERITY_LABEL: Record<ErcSeverity, string> = {
  error: "Errors",
  warning: "Warnings",
  exclusion: "Exclusions",
  ignore: "Ignored",
};

const SEVERITY_COLOR: Record<ErcSeverity, string> = {
  error: "text-red-400",
  warning: "text-amber-400",
  exclusion: "text-slate-400",
  ignore: "text-slate-500",
};

/**
 * `ErcPanel` (M1-T-06) — sidebar panel that runs `kc_erc` against
 * the active project and renders the violations grouped by severity.
 * Read-only — auto-approved by the M1-P-06 permission gate.
 *
 * Clicking an issue calls `onFlyTo` so the parent's
 * `<SchematicCanvas>` can camera-jump to the violation position.
 */
export function ErcPanel(props: ErcPanelProps) {
  const {
    projectId,
    projectPath,
    apiBase = "/api/connector",
    fetcher,
    onFlyTo,
  } = props;

  const [report, setReport] = useState<ErcReport | null>(null);
  const [pending, setPending] = useState(false);
  const [transportError, setTransportError] = useState<string | null>(null);
  const fetchImpl = useMemo(
    () => fetcher ?? globalThis.fetch.bind(globalThis),
    [fetcher],
  );

  const runErc = useCallback(async () => {
    setPending(true);
    setTransportError(null);
    try {
      const resp = await fetchImpl(`${apiBase}/tools/erc`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ project_path: projectPath }),
      });
      const body = (await resp.json()) as ErcReport & { detail?: string };
      if (!resp.ok) {
        throw new Error(
          body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`,
        );
      }
      setReport(body);
    } catch (err) {
      setTransportError(err instanceof Error ? err.message : String(err));
      setReport(null);
    } finally {
      setPending(false);
    }
  }, [apiBase, fetchImpl, projectPath]);

  const groups = useMemo(
    () => groupBySeverity(report?.issues ?? []),
    [report],
  );

  return (
    <Panel
      data-testid="erc-panel"
      aria-label="kiclaude ERC results"
      density="compact"
      maxBodyHeight="70vh"
      className="w-90"
      title={
        <div className="flex items-baseline gap-2">
          <Text variant="h4">ERC</Text>
          <Text variant="caption" className="truncate">
            {projectId}
          </Text>
        </div>
      }
      actions={
        <button
          type="button"
          data-testid="erc-run-button"
          onClick={runErc}
          disabled={pending}
          className={`inline-flex h-7 items-center rounded px-2.5 text-xs font-semibold text-white ${
            pending
              ? "cursor-wait bg-[var(--text)]/40"
              : "cursor-pointer bg-sky-600 hover:bg-sky-500"
          }`}
        >
          {pending ? "Running…" : "Run ERC"}
        </button>
      }
    >
      <div className="flex flex-col gap-2">
        {transportError ? (
          <p
            data-testid="erc-transport-error"
            className="m-0 text-xs text-red-500"
          >
            {transportError}
          </p>
        ) : null}
        {report && !report.ok ? (
          <p
            data-testid="erc-tool-error"
            className="m-0 text-xs text-red-500"
          >
            {report.error ?? "ERC tool reported a failure"}
          </p>
        ) : null}
        {report && report.ok ? (
          <div
            data-testid="erc-summary"
            className="flex flex-wrap gap-2 text-xs"
          >
            {SEVERITY_ORDER.map((sev) => (
              <span key={sev} data-testid={`erc-summary-${sev}`}>
                <span className={`mr-1 ${SEVERITY_COLOR[sev]}`}>●</span>
                {SEVERITY_LABEL[sev]}: {groups[sev]?.length ?? 0}
              </span>
            ))}
          </div>
        ) : null}
        <ul
          data-testid="erc-issue-list"
          className="m-0 list-none p-0"
        >
          {SEVERITY_ORDER.map((sev) => {
            const items = groups[sev] ?? [];
            if (items.length === 0) return null;
            return (
              <li
                key={sev}
                data-testid={`erc-group-${sev}`}
                className="mb-1"
              >
                <header className={`py-1 ${SEVERITY_COLOR[sev]}`}>
                  {SEVERITY_LABEL[sev]} ({items.length})
                </header>
                <ul className="m-0 list-none p-0">
                  {items.map((issue, i) => (
                    <li
                      key={`${sev}-${i}-${issue.type}`}
                      data-testid={`erc-issue-${sev}-${i}`}
                    >
                      <button
                        type="button"
                        onClick={() => onFlyTo?.(issue.sheet, issue.position_mm)}
                        className="mb-1 block w-full cursor-pointer rounded border border-[var(--border)] bg-[var(--code-bg)] p-1.5 text-left text-[13px] text-[var(--text-h)] hover:border-[var(--accent-border)]"
                      >
                        <span className="font-semibold">{issue.type}</span>
                        <span className="ml-1.5 text-[11px] text-[var(--text)]/70">
                          @ {issue.sheet || "/"} ({issue.position_mm[0]},{" "}
                          {issue.position_mm[1]})
                        </span>
                        <div className="mt-0.5 text-xs text-[var(--text)]/90">
                          {issue.description}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              </li>
            );
          })}
          {report && report.ok && (report.issues?.length ?? 0) === 0 ? (
            <li
              data-testid="erc-clean"
              className="p-2 italic text-emerald-500"
            >
              ERC clean — no violations.
            </li>
          ) : null}
        </ul>
      </div>
    </Panel>
  );
}

function groupBySeverity(issues: ErcIssue[]): Partial<Record<ErcSeverity, ErcIssue[]>> {
  const out: Partial<Record<ErcSeverity, ErcIssue[]>> = {};
  for (const issue of issues) {
    const key = (SEVERITY_ORDER.includes(issue.severity)
      ? issue.severity
      : "warning") as ErcSeverity;
    out[key] = [...(out[key] ?? []), issue];
  }
  return out;
}
