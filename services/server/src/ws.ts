import type { Hono } from "hono";

/** Minimal shape of the `@hono/node-ws` connection passed to callbacks. */
export interface WsConn {
  send(data: string | ArrayBuffer): void;
  close(code?: number, reason?: string): void;
}

// `upgradeWebSocket` returns a Hono handler when called with a factory.
// Its exact generic types are deep inside `@hono/node-ws` — we type-erase
// at this boundary so this file doesn't need to mirror their types.
type UpgradeFactory = (handler: (c: unknown) => WsEventCallbacks) => (c: unknown) => Response;

interface WsEventCallbacks {
  onOpen?(event: Event, ws: WsConn): void;
  onMessage?(event: { data: string | ArrayBuffer | Blob }, ws: WsConn): void;
  onClose?(event: { code: number; reason: string }, ws: WsConn): void;
}

/**
 * Register a `WS /ws` echo route — each frame received from the client
 * is sent back unchanged. M0 just satisfies the connectivity contract;
 * the real bidirectional agent <-> client wiring lands in M0-T-03
 * (ChatSidebar) on top of this surface.
 */
export function registerWebSocketRoutes(app: Hono, upgrade: UpgradeFactory): void {
  const handler = upgrade(() => ({
    onOpen(_evt, ws) {
      ws.send(JSON.stringify({ kind: "hello", service: "kiclaude-server" }));
    },
    onMessage(evt, ws) {
      if (typeof evt.data === "string" || evt.data instanceof ArrayBuffer) {
        ws.send(evt.data);
      }
    },
  }));
  // Hono's typed `.get` insists on its own context type, but the value
  // we get back from `upgradeWebSocket` is the correctly-shaped handler.
  // Cast through `any` here only — surface API is preserved by tests.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (app.get as (path: string, h: unknown) => unknown)("/ws", handler);
}
