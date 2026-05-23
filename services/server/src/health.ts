import { defaultBackends, type BackendRoute } from "./proxy.js";

export interface BackendHealth {
  ok: boolean;
  status: number;
  body: unknown;
  error?: string;
}

export interface AggregatedHealth {
  ok: boolean;
  service: string;
  version: string;
  backends: Record<string, BackendHealth>;
}

/**
 * Fan out `GET ${origin}/health` to every backend in `backends`,
 * collect results in parallel, and report `ok=true` iff every backend
 * returned `ok=true` (or matches the `{ok:true,...}` envelope).
 */
export async function aggregateHealth(
  version: string,
  backends: BackendRoute[] = defaultBackends(),
  fetchImpl: typeof fetch = fetch,
  timeoutMs = 2_000,
): Promise<AggregatedHealth> {
  const entries = await Promise.all(
    backends.map(async (b) => {
      try {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), timeoutMs);
        const resp = await fetchImpl(`${b.origin}/health`, { signal: ctrl.signal });
        clearTimeout(t);
        const body: unknown = await resp.json().catch(() => ({}));
        const ok = resp.ok && envelopeOk(body);
        return [b.name, { ok, status: resp.status, body }] as const;
      } catch (err) {
        return [
          b.name,
          {
            ok: false,
            status: 0,
            body: null,
            error: err instanceof Error ? err.message : String(err),
          },
        ] as const;
      }
    }),
  );
  const map = Object.fromEntries(entries) as Record<string, BackendHealth>;
  return {
    ok: Object.values(map).every((h) => h.ok),
    service: "server",
    version,
    backends: map,
  };
}

function envelopeOk(body: unknown): boolean {
  if (typeof body !== "object" || body === null) return false;
  return (body as { ok?: unknown }).ok === true;
}
