import { describe, expect, it, vi } from "vitest";

import { aggregateHealth } from "./health.js";
import type { BackendRoute } from "./proxy.js";
import { createApp } from "./index.js";

const backends: BackendRoute[] = [
  { name: "agent", prefix: "/api/agent", origin: "http://agent.test" },
  { name: "kiserver", prefix: "/api/server", origin: "http://kiserver.test" },
  { name: "kiconnector", prefix: "/api/connector", origin: "http://kiconnector.test" },
];

describe("GET /", () => {
  it("returns the service envelope", async () => {
    const app = createApp();
    const res = await app.request("/");
    expect(res.status).toBe(200);
    const body = (await res.json()) as { service: string; ok: boolean };
    expect(body.service).toBe("kiclaude-server");
    expect(body.ok).toBe(true);
  });
});

describe("aggregateHealth", () => {
  it("reports ok=true when every backend's /health is ok", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      void url;
      return new Response(JSON.stringify({ ok: true, service: "x" }), { status: 200 });
    }) as unknown as typeof fetch;
    const result = await aggregateHealth("0.1.0", backends, fetchMock);
    expect(result.ok).toBe(true);
    expect(result.service).toBe("server");
    expect(Object.keys(result.backends).sort()).toEqual(["agent", "kiconnector", "kiserver"]);
    for (const name of ["agent", "kiconnector", "kiserver"]) {
      expect(result.backends[name]?.ok).toBe(true);
    }
  });

  it("reports ok=false when any backend is down", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("agent.test")) {
        throw new Error("ECONNREFUSED");
      }
      return new Response(JSON.stringify({ ok: true }), { status: 200 });
    }) as unknown as typeof fetch;
    const result = await aggregateHealth("0.1.0", backends, fetchMock);
    expect(result.ok).toBe(false);
    expect(result.backends.agent?.ok).toBe(false);
    expect(result.backends.kiserver?.ok).toBe(true);
  });

  it("reports ok=false when a backend returns 500", async () => {
    const fetchMock = vi.fn(async () => {
      return new Response("{}", { status: 500 });
    }) as unknown as typeof fetch;
    const result = await aggregateHealth("0.1.0", backends, fetchMock);
    expect(result.ok).toBe(false);
  });
});
