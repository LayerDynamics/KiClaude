import { serve } from "@hono/node-server";
import { createNodeWebSocket } from "@hono/node-ws";
import { Hono } from "hono";

import { aggregateHealth } from "./health.js";
import { defaultBackends, forwardRequest } from "./proxy.js";
import { registerApprovalRoutes } from "./routes/approval.js";
import { registerUiToolRoutes } from "./routes/ui_tools.js";
import { CrdtHub, multiplayerEnabled } from "./crdt.js";
import { registerCrdtRoutes, registerWebSocketRoutes } from "./ws.js";

const SERVER_VERSION = "0.1.0";
const DEFAULT_PORT = Number.parseInt(process.env.KICLAUDE_GATEWAY_PORT ?? "8080", 10);

export function createApp(): Hono {
  const app = new Hono();

  app.get("/", (c) =>
    c.json({ service: "kiclaude-server", version: SERVER_VERSION, ok: true }),
  );

  app.get("/api/health", async (c) => {
    const health = await aggregateHealth(SERVER_VERSION);
    return c.json(health, health.ok ? 200 : 503);
  });

  // UI-only tool routes — registered BEFORE the catch-all proxy so
  // the gateway can enforce its allowlist (SPEC §1.4 #4) instead of
  // blindly forwarding `/api/ui/<anything>` to kiserver.
  registerUiToolRoutes(app);
  // M1-P-06 permission gate: agent → gateway → WS → UI back-channel.
  registerApprovalRoutes(app);

  // Catch-all proxy routes: /api/agent/* → agent service, etc.
  for (const backend of defaultBackends()) {
    app.all(`${backend.prefix}/*`, async (c) => {
      try {
        const upstream = await forwardRequest(c.req.raw, backend);
        return new Response(upstream.body, {
          status: upstream.status,
          statusText: upstream.statusText,
          headers: upstream.headers,
        });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        return c.json(
          {
            ok: false,
            service: "server",
            backend: backend.name,
            error: `upstream unreachable: ${message}`,
          },
          502,
        );
      }
    });
  }

  return app;
}

export function startServer(port: number = DEFAULT_PORT): { close: () => void } {
  const app = createApp();
  const { injectWebSocket, upgradeWebSocket } = createNodeWebSocket({ app });
  registerWebSocketRoutes(app, upgradeWebSocket as never);
  // FR-081 multiplayer relay — opt-in (off by default, FP#8). See
  // ADR-0001 (Yjs) + `crdt.ts`.
  if (multiplayerEnabled()) {
    registerCrdtRoutes(app, upgradeWebSocket as never, new CrdtHub());
    process.stdout.write("kiclaude-server: CRDT multiplayer enabled (/crdt/:projectId)\n");
  }

  const server = serve({ fetch: app.fetch, port });
  injectWebSocket(server);
  process.stdout.write(`kiclaude-server: listening on :${port}\n`);
  return {
    close() {
      server.close();
    },
  };
}

// CLI entry point: `node dist/index.js` or `pnpm -F server dev`.
const invokedDirectly = (() => {
  try {
    const { argv } = process;
    if (argv.length < 2) return false;
    const entry = argv[1];
    if (!entry) return false;
    return entry.endsWith("/index.js") || entry.endsWith("/index.ts");
  } catch {
    return false;
  }
})();

if (invokedDirectly) {
  startServer();
}
