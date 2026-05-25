import type { Hono } from "hono";

import { CrdtHub, type CrdtMessage } from "./crdt.js";

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

/** Read a route param off the type-erased Hono context. */
function ctxParam(c: unknown, name: string): string {
  const req = (c as { req?: { param?: (n: string) => string | undefined } }).req;
  return req?.param?.(name) ?? "";
}

/** Validate an inbound frame into a {@link CrdtMessage}, or `null`. */
export function parseCrdtMessage(raw: unknown): CrdtMessage | null {
  if (typeof raw !== "string") return null;
  let doc: unknown;
  try {
    doc = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof doc !== "object" || doc === null) return null;
  const obj = doc as Record<string, unknown>;
  if (obj.kind === "sync-step1" && typeof obj.sv === "string") {
    return { kind: "sync-step1", sv: obj.sv };
  }
  if (
    (obj.kind === "sync-step2" || obj.kind === "update") &&
    typeof obj.update === "string"
  ) {
    return { kind: obj.kind, update: obj.update };
  }
  return null;
}

/**
 * Register the `WS /crdt/:projectId` multiplayer relay (FR-081). Each
 * connection joins its project's {@link CrdtRoom} on the shared
 * {@link CrdtHub}; frames are Yjs-update JSON envelopes relayed by the
 * room. Off by default — `startServer` only calls this when
 * `KICLAUDE_MULTIPLAYER` is set (see `crdt.ts::multiplayerEnabled`).
 */
export function registerCrdtRoutes(app: Hono, upgrade: UpgradeFactory, hub: CrdtHub): void {
  const handler = upgrade((c) => {
    const projectId = ctxParam(c, "projectId");
    const room = hub.room(projectId);
    // One stable send fn per connection so the room can skip the origin
    // when fanning out (`dispatch` compares by identity).
    let send: ((message: CrdtMessage) => void) | null = null;
    let off: (() => void) | null = null;
    return {
      onOpen(_evt, ws) {
        send = (message) => ws.send(JSON.stringify(message));
        off = room.join(send);
        // Greet the joiner so it can pull the authoritative state.
        for (const out of room.greeting()) {
          if (out.to === "sender") send(out.message);
        }
      },
      onMessage(evt) {
        if (!send) return;
        const message = parseCrdtMessage(evt.data);
        if (message === null) return;
        room.dispatch(room.process(message), send);
      },
      onClose() {
        off?.();
        hub.release(projectId);
      },
    };
  });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (app.get as (path: string, h: unknown) => unknown)("/crdt/:projectId", handler);
}
