/**
 * `BomView` (M3-T-08) — live distributor-priced bill of materials.
 *
 * Data path:
 *
 *   GET /api/server/project/<id>/bom/price?qty_multiplier=N
 *     → kiserver groups project.footprints by mpn, sums qty
 *     → kiserver runs the M3-P-05 aggregator (Digi-Key today, more
 *       distributors when M3-P-01/02/04 land)
 *     → JSON `{bom_lines, pricing: {parts, distributor_totals_usd,
 *        grand_total_usd, missing_mpns, errors}}`
 *
 * Renders:
 *
 *   - header: line count · part count · grand total · cart-split
 *     ("Buy from Digi-Key: $42.18, Mouser: $17.05") · refresh
 *   - one row per MPN: refdes count (qty per board) · winning
 *     distributor · unit price · stock · lifecycle · line total
 *   - footer: missing MPNs (yellow) + per-distributor error list
 *
 * The qty-per-board picker scales every line so the user can price
 * a 1-of, 100-of, or 1k-of build in one click.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useProjectStore } from "../../stores/projectStore";

export interface BomLine {
  mpn: string;
  qty: number;
  refdes_count: number;
}

export interface BomCheapest {
  distributor: string;
  distributor_sku: string;
  manufacturer: string;
  description: string;
  in_stock_qty: number;
  lifecycle: string;
  product_url: string;
  unit_price_usd: number | null;
}

export interface BomPart {
  mpn: string;
  requested_qty: number;
  cheapest: BomCheapest | null;
  line_total_usd: number | null;
  errors: Record<string, string>;
  quote_count: number;
}

export interface BomPricing {
  parts: BomPart[];
  distributor_totals_usd: Record<string, number>;
  grand_total_usd: number;
  missing_mpns: string[];
  errors: Record<string, string[]>;
}

interface BomResponse {
  ok?: boolean;
  project_id?: string;
  bom_lines?: BomLine[];
  pricing?: BomPricing;
  error?: string;
  detail?: string;
}

export interface BomViewProps {
  projectId: string;
  /** Gateway base — defaults to `/api/server`. */
  apiBase?: string;
  fetcher?: typeof fetch;
  className?: string;
}

const LIFECYCLE_COLOR: Record<string, string> = {
  active: "#34d399",
  nrnd: "#fbbf24",
  obsolete: "#ff7875",
  preview: "#60a5fa",
  unknown: "#9ca3af",
};

export function BomView(props: BomViewProps) {
  const { projectId, apiBase = "/api/server", fetcher, className } = props;
  const project = useProjectStore((s) => s.project);
  const fetchImpl = fetcher ?? globalThis.fetch.bind(globalThis);

  const [qtyMultiplier, setQtyMultiplier] = useState<number>(1);
  const [lines, setLines] = useState<BomLine[]>([]);
  const [pricing, setPricing] = useState<BomPricing | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lastRequestId = useRef(0);

  const load = useCallback(
    async (force: boolean) => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        params.set("qty_multiplier", String(qtyMultiplier));
        if (force) params.set("force_refresh", "true");
        const url = `${apiBase}/project/${encodeURIComponent(projectId)}/bom/price?${params}`;
        const resp = await fetchImpl(url, { method: "GET" });
        const body = (await resp.json()) as BomResponse;
        if (!resp.ok || body.ok === false) {
          throw new Error(body.error ?? body.detail ?? `${resp.status} ${resp.statusText}`);
        }
        setLines(body.bom_lines ?? []);
        setPricing(body.pricing ?? null);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setLoading(false);
      }
    },
    [apiBase, fetchImpl, projectId, qtyMultiplier],
  );

  // Auto-load on mount + whenever the qty multiplier changes.
  useEffect(() => {
    void load(false);
  }, [load]);

  const totalRefdesCount = useMemo(
    () => lines.reduce((sum, l) => sum + l.refdes_count, 0),
    [lines],
  );

  const sortedDistributors = useMemo(() => {
    if (!pricing) return [];
    return Object.entries(pricing.distributor_totals_usd).sort((a, b) => b[1] - a[1]);
  }, [pricing]);

  const errorEntries = useMemo(() => {
    if (!pricing) return [];
    return Object.entries(pricing.errors);
  }, [pricing]);

  if (!project) {
    return (
      <div data-testid="bom-view" data-status="empty" className={className} style={panelStyle}>
        <p style={{ padding: 12, color: "#9ca3af", fontSize: 12, margin: 0 }}>
          No project loaded — open a project to price its BOM.
        </p>
      </div>
    );
  }

  return (
    <div
      data-testid="bom-view"
      data-status={loading ? "loading" : "ready"}
      className={className}
      style={panelStyle}
    >
      <header style={headerStyle}>
        <span style={{ flex: 1 }}>BOM pricing</span>
        <label style={qtyLabelStyle}>
          Qty per build:
          <input
            type="number"
            min={1}
            step={1}
            value={qtyMultiplier}
            onChange={(e) =>
              setQtyMultiplier(Math.max(1, Number.parseInt(e.target.value || "1", 10) || 1))
            }
            data-testid="bom-qty"
            style={qtyInputStyle}
          />
        </label>
        <button
          type="button"
          onClick={() => void load(true)}
          disabled={loading}
          style={refreshButtonStyle(loading)}
          data-testid="bom-refresh"
        >
          {loading ? "Loading…" : "Refresh"}
        </button>
      </header>

      <div style={summaryRowStyle} data-testid="bom-summary">
        <span>
          {lines.length} unique MPN{lines.length === 1 ? "" : "s"}
        </span>
        <span>·</span>
        <span>{totalRefdesCount} placements / board</span>
        <span>·</span>
        <span data-testid="bom-grand-total">
          Grand total:{" "}
          {pricing ? `$${pricing.grand_total_usd.toFixed(2)}` : "—"}
        </span>
        {sortedDistributors.length > 0 ? (
          <span data-testid="bom-cart-split">
            {" "}— cart split:{" "}
            {sortedDistributors
              .map(([dist, total]) => `${dist} $${total.toFixed(2)}`)
              .join(" · ")}
          </span>
        ) : null}
      </div>

      {error ? (
        <div data-testid="bom-error" style={errorRowStyle}>
          {error}
        </div>
      ) : null}

      {pricing && pricing.missing_mpns.length > 0 ? (
        <div data-testid="bom-missing" style={warnRowStyle}>
          No distributor quotes for: {pricing.missing_mpns.join(", ")}
        </div>
      ) : null}

      {errorEntries.length > 0 ? (
        <div data-testid="bom-distributor-errors" style={warnRowStyle}>
          {errorEntries.map(([dist, msgs]) => (
            <div key={dist}>
              <strong>{dist}:</strong> {msgs.join("; ")}
            </div>
          ))}
        </div>
      ) : null}

      <table style={tableStyle}>
        <thead>
          <tr style={{ color: "#9ca3af" }}>
            <th style={thStyle}>MPN</th>
            <th style={thStyle}>Per board</th>
            <th style={thStyle}>Qty</th>
            <th style={thStyle}>Distributor</th>
            <th style={thStyle}>Unit $</th>
            <th style={thStyle}>Line $</th>
            <th style={thStyle}>Stock</th>
            <th style={thStyle}>Lifecycle</th>
          </tr>
        </thead>
        <tbody>
          {lines.length === 0 && !loading ? (
            <tr>
              <td colSpan={8} style={{ ...tdStyle, color: "#9ca3af", textAlign: "center" }}>
                No MPNs on this board yet — assign MPNs to footprints to price the BOM.
              </td>
            </tr>
          ) : (
            lines.map((line) => {
              const part = pricing?.parts.find((p) => p.mpn === line.mpn);
              const cheapest = part?.cheapest ?? null;
              const errs = part?.errors ?? {};
              return (
                <tr key={line.mpn} data-testid="bom-row" data-mpn={line.mpn}>
                  <td style={tdStyle}>{line.mpn}</td>
                  <td style={tdStyle}>{line.refdes_count}</td>
                  <td style={tdStyle}>{line.qty}</td>
                  <td style={tdStyle}>
                    {cheapest ? (
                      <a
                        href={cheapest.product_url}
                        target="_blank"
                        rel="noreferrer noopener"
                        style={distLinkStyle}
                        data-testid="bom-distributor-link"
                      >
                        {cheapest.distributor} {cheapest.distributor_sku}
                      </a>
                    ) : (
                      <span data-testid="bom-no-quote" style={{ color: "#fbbf24" }}>
                        no quote
                      </span>
                    )}
                  </td>
                  <td style={tdStyle}>
                    {cheapest?.unit_price_usd != null
                      ? `$${cheapest.unit_price_usd.toFixed(4)}`
                      : "—"}
                  </td>
                  <td style={tdStyle}>
                    {part?.line_total_usd != null ? `$${part.line_total_usd.toFixed(2)}` : "—"}
                  </td>
                  <td style={tdStyle}>{cheapest ? cheapest.in_stock_qty : "—"}</td>
                  <td
                    style={{
                      ...tdStyle,
                      color: LIFECYCLE_COLOR[cheapest?.lifecycle ?? "unknown"],
                    }}
                  >
                    {cheapest?.lifecycle ?? "—"}
                    {Object.keys(errs).length > 0 ? (
                      <span
                        title={Object.entries(errs)
                          .map(([d, m]) => `${d}: ${m}`)
                          .join("\n")}
                        style={{ color: "#ff7875", marginLeft: 6 }}
                        data-testid="bom-row-error"
                      >
                        ⚠
                      </span>
                    ) : null}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}

const panelStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  background: "#10131a",
  border: "1px solid #1f2330",
  borderRadius: 6,
  overflow: "auto",
  fontSize: 12,
};

const headerStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "8px 12px",
  borderBottom: "1px solid #1f2330",
  fontWeight: 600,
  color: "#cbd5e1",
  letterSpacing: 0.4,
  textTransform: "uppercase",
  background: "#161b25",
};

const qtyLabelStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  fontSize: 11,
  textTransform: "none",
  fontWeight: 400,
  color: "#cbd5e1",
};

const qtyInputStyle: React.CSSProperties = {
  width: 70,
  padding: "4px 6px",
  background: "#0d1018",
  color: "#f9fafb",
  border: "1px solid #1f2330",
  borderRadius: 3,
  fontSize: 12,
};

function refreshButtonStyle(loading: boolean): React.CSSProperties {
  return {
    padding: "4px 12px",
    background: loading ? "#1f2937" : "#1e40af",
    color: loading ? "#9ca3af" : "#f9fafb",
    border: "none",
    borderRadius: 3,
    cursor: loading ? "default" : "pointer",
    fontSize: 11,
  };
}

const summaryRowStyle: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  padding: "6px 12px",
  background: "#0e1119",
  borderBottom: "1px solid #1f2330",
  color: "#cbd5e1",
  fontSize: 11,
};

const errorRowStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "rgba(255, 77, 79, 0.15)",
  color: "#ff7875",
  fontSize: 11,
  borderBottom: "1px solid #401b1b",
};

const warnRowStyle: React.CSSProperties = {
  padding: "6px 12px",
  background: "rgba(251, 191, 36, 0.12)",
  color: "#fbbf24",
  fontSize: 11,
  borderBottom: "1px solid #4a3a0c",
};

const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 12,
};

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "6px 8px",
  fontSize: 10,
  textTransform: "uppercase",
  letterSpacing: 0.4,
  borderBottom: "1px solid #1f2330",
};

const tdStyle: React.CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid #1a1f2a",
  color: "#e2e8f0",
};

const distLinkStyle: React.CSSProperties = {
  color: "#60a5fa",
  textDecoration: "none",
};
