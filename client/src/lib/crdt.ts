/**
 * Client CRDT session (FR-081) — multiplayer document sync via Yjs.
 *
 * Wraps a `Y.Doc` whose `project` map mirrors the KCIR project, and
 * drives the same update-based sync handshake the gateway relay speaks
 * (`services/server/src/crdt.ts`): on connect it offers its state
 * vector, replies to a peer's vector with the updates that peer lacks,
 * applies inbound updates, and broadcasts its own local changes.
 *
 * The transport is injectable so this is unit-testable peer-to-peer
 * without a real WebSocket. {@link openCrdtWebSocket} provides the
 * production transport against `WS /crdt/:projectId`. Multiplayer is
 * opt-in (ADR-0001); nothing here runs unless a session is constructed.
 */

import * as Y from "yjs";

/** The shared `Y.Map` name mirroring the project (matches the server). */
export const PROJECT_MAP = "project";

export type CrdtMessage =
  | { kind: "sync-step1"; sv: string }
  | { kind: "sync-step2"; update: string }
  | { kind: "update"; update: string };

/** Pluggable message transport (a WebSocket in production, a direct
 * relay in tests). */
export interface CrdtTransport {
  send(message: CrdtMessage): void;
  onMessage(handler: (message: CrdtMessage) => void): void;
  close(): void;
}

// Browser-safe base64 (no Node Buffer). btoa/atob exist in every target
// browser and in the happy-dom test env.
export function encodeU8(u8: Uint8Array): string {
  let s = "";
  for (const b of u8) s += String.fromCharCode(b);
  return btoa(s);
}

export function decodeU8(b64: string): Uint8Array {
  const s = atob(b64);
  const u8 = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) u8[i] = s.charCodeAt(i);
  return u8;
}

const REMOTE_ORIGIN = "crdt-remote";

export class CrdtSession {
  readonly doc: Y.Doc;
  private readonly transport: CrdtTransport;
  private closed = false;

  constructor(transport: CrdtTransport, doc: Y.Doc = new Y.Doc()) {
    this.doc = doc;
    this.transport = transport;

    transport.onMessage((message) => this.handle(message));

    // Broadcast local edits. Updates we applied from a peer carry the
    // REMOTE_ORIGIN tag and must NOT be echoed back (no feedback loop).
    this.doc.on("update", (update: Uint8Array, origin: unknown) => {
      if (this.closed || origin === REMOTE_ORIGIN) return;
      transport.send({ kind: "update", update: encodeU8(update) });
    });

    // Kick off sync: tell the peer what we already have.
    transport.send({ kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(this.doc)) });
  }

  /** The shared project map — read/write to collaborate. */
  get project(): Y.Map<unknown> {
    return this.doc.getMap(PROJECT_MAP);
  }

  private handle(message: CrdtMessage): void {
    if (this.closed) return;
    switch (message.kind) {
      case "sync-step1": {
        // Peer's state vector → send it the updates it's missing.
        const diff = Y.encodeStateAsUpdate(this.doc, decodeU8(message.sv));
        this.transport.send({ kind: "sync-step2", update: encodeU8(diff) });
        break;
      }
      case "sync-step2":
      case "update": {
        Y.applyUpdate(this.doc, decodeU8(message.update), REMOTE_ORIGIN);
        break;
      }
    }
  }

  close(): void {
    // Flip the guard first so any in-flight inbound frame is ignored,
    // then drop the transport. The doc is left intact (GC reclaims it
    // when the session is dropped) so a consumer can still read the last
    // converged state after disconnect.
    this.closed = true;
    this.transport.close();
  }
}

/** Production transport: a WebSocket to the gateway's `WS /crdt/:id`
 * relay. `base` defaults to the page's ws(s):// origin. */
export function openCrdtWebSocket(
  projectId: string,
  base: string = defaultWsBase(),
): CrdtTransport {
  const ws = new WebSocket(`${base}/crdt/${encodeURIComponent(projectId)}`);
  let handler: ((message: CrdtMessage) => void) | null = null;
  const outbox: string[] = [];
  ws.addEventListener("open", () => {
    for (const frame of outbox.splice(0)) ws.send(frame);
  });
  ws.addEventListener("message", (evt: MessageEvent) => {
    if (typeof evt.data !== "string" || !handler) return;
    try {
      handler(JSON.parse(evt.data) as CrdtMessage);
    } catch {
      /* ignore malformed frames */
    }
  });
  return {
    send(message) {
      const frame = JSON.stringify(message);
      if (ws.readyState === WebSocket.OPEN) ws.send(frame);
      else outbox.push(frame);
    },
    onMessage(h) {
      handler = h;
    },
    close() {
      ws.close();
    },
  };
}

function defaultWsBase(): string {
  const loc = globalThis.location;
  const proto = loc && loc.protocol === "https:" ? "wss:" : "ws:";
  const host = loc ? loc.host : "localhost:8080";
  return `${proto}//${host}`;
}
