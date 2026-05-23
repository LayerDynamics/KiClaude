/** Backend service the gateway proxies to. */
export interface BackendRoute {
  name: string;
  /** Path prefix on the gateway. Requests at `${prefix}/...` are
   * forwarded to the matching backend with the prefix stripped. */
  prefix: string;
  /** Full origin (scheme + host + port) of the backend. */
  origin: string;
}

/** Default M0 backend topology — all three services on `localhost`. */
export function defaultBackends(): BackendRoute[] {
  return [
    { name: "agent", prefix: "/api/agent", origin: agentOrigin() },
    { name: "kiserver", prefix: "/api/server", origin: kiserverOrigin() },
    { name: "kiconnector", prefix: "/api/connector", origin: kiconnectorOrigin() },
  ];
}

export function agentOrigin(): string {
  return process.env.KICLAUDE_AGENT_ORIGIN ?? "http://127.0.0.1:8082";
}

export function kiserverOrigin(): string {
  return process.env.KICLAUDE_KISERVER_ORIGIN ?? "http://127.0.0.1:8083";
}

export function kiconnectorOrigin(): string {
  return process.env.KICLAUDE_KICONNECTOR_ORIGIN ?? "http://127.0.0.1:8084";
}

/**
 * Forward a Fetch `Request` to `backend.origin`, stripping `backend.prefix`
 * from the pathname. Preserves method, headers, and body. Returns the
 * raw upstream `Response` so the handler can stream it back to the
 * caller without re-buffering.
 *
 * @throws when the upstream is unreachable — the caller should turn
 * this into a 502/504 envelope.
 */
export async function forwardRequest(req: Request, backend: BackendRoute): Promise<Response> {
  const inUrl = new URL(req.url);
  const stripped = inUrl.pathname.startsWith(backend.prefix)
    ? inUrl.pathname.slice(backend.prefix.length) || "/"
    : inUrl.pathname;
  const upstream = new URL(stripped, backend.origin);
  upstream.search = inUrl.search;

  const init: RequestInit = {
    method: req.method,
    headers: cloneHeaders(req.headers),
    redirect: "manual",
  };
  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.arrayBuffer();
  }
  return await fetch(upstream.toString(), init);
}

function cloneHeaders(headers: Headers): Headers {
  const out = new Headers();
  headers.forEach((value, name) => {
    if (name.toLowerCase() === "host") return;
    out.set(name, value);
  });
  return out;
}
