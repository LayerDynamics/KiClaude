/**
 * CRDT multiplayer relay (FR-081) — off by default.
 *
 * A per-project {@link CrdtRoom} holds an authoritative Yjs `Y.Doc`.
 * Clients converge against it with an update-based sync handshake driven
 * by Yjs's own stable primitives (`encodeStateVector` /
 * `encodeStateAsUpdate` / `applyUpdate`), so we stay protocol-compatible
 * with Yjs without adopting the full `y-websocket` server (which expects
 * a raw `ws` socket; the gateway runs on `@hono/node-ws`).
 *
 * Messages are a tiny JSON envelope carrying base64-encoded Yjs binary
 * updates — transport-agnostic and trivially unit-testable in-process.
 * See ADR-0001 for the engine choice and the JSON-level (vs KCIR-aware)
 * scope. Multiplayer is gated behind a feature flag; with it off the
 * single-editor + Git story (FR-081 v1) is unchanged.
 */

import * as Y from "yjs";

/** The shared `Y.Map` name that mirrors the project document. */
export const PROJECT_MAP = "project";

export type CrdtMessage =
  | { kind: "sync-step1"; sv: string } // base64 state vector
  | { kind: "sync-step2"; update: string } // base64 update (diff for the peer)
  | { kind: "update"; update: string }; // base64 update (a live change)

/** Where an outbound message should go relative to the sender. */
export interface CrdtOutgoing {
  to: "sender" | "others";
  message: CrdtMessage;
}

export function encodeU8(u8: Uint8Array): string {
  return Buffer.from(u8).toString("base64");
}

export function decodeU8(b64: string): Uint8Array {
  return new Uint8Array(Buffer.from(b64, "base64"));
}

/**
 * One project's authoritative document + connection set. The sync logic
 * (`greeting` / `process`) is pure with respect to I/O — it returns the
 * messages to send rather than sending them — so it unit-tests without a
 * socket.
 */
export class CrdtRoom {
  readonly id: string;
  readonly doc: Y.Doc;
  private readonly conns = new Set<(message: CrdtMessage) => void>();

  constructor(id: string) {
    this.id = id;
    this.doc = new Y.Doc();
  }

  /** Register a connection's send fn; returns an unsubscribe. */
  join(send: (message: CrdtMessage) => void): () => void {
    this.conns.add(send);
    return () => {
      this.conns.delete(send);
    };
  }

  get connectionCount(): number {
    return this.conns.size;
  }

  /** The handshake a freshly-joined peer should receive: our state
   * vector, so it can reply with the updates we're missing. */
  greeting(): CrdtOutgoing[] {
    return [{ to: "sender", message: { kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(this.doc)) } }];
  }

  /** Process one inbound message; mutate the authoritative doc and
   * return the outbound messages (the caller routes by `to`). */
  process(message: CrdtMessage): CrdtOutgoing[] {
    switch (message.kind) {
      case "sync-step1": {
        // Peer told us its state vector → send it exactly the updates it
        // lacks, and our own SV so it reciprocates.
        const diff = Y.encodeStateAsUpdate(this.doc, decodeU8(message.sv));
        return [
          { to: "sender", message: { kind: "sync-step2", update: encodeU8(diff) } },
          { to: "sender", message: { kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(this.doc)) } },
        ];
      }
      case "sync-step2":
      case "update": {
        Y.applyUpdate(this.doc, decodeU8(message.update), "remote");
        // Fan the raw update out to the other peers so they converge.
        return [{ to: "others", message: { kind: "update", update: message.update } }];
      }
    }
  }

  /** Route the outputs of {@link process}: `sender` back to the origin,
   * `others` to every connection except the origin. */
  dispatch(outgoing: CrdtOutgoing[], origin: (message: CrdtMessage) => void): void {
    for (const out of outgoing) {
      if (out.to === "sender") {
        origin(out.message);
      } else {
        for (const send of this.conns) {
          if (send !== origin) send(out.message);
        }
      }
    }
  }
}

/** Registry of per-project rooms. Rooms are created lazily and dropped
 * when their last connection leaves. */
export class CrdtHub {
  private readonly rooms = new Map<string, CrdtRoom>();

  room(id: string): CrdtRoom {
    let room = this.rooms.get(id);
    if (!room) {
      room = new CrdtRoom(id);
      this.rooms.set(id, room);
    }
    return room;
  }

  /** Drop a room once empty so an idle project doesn't pin its doc. */
  release(id: string): void {
    const room = this.rooms.get(id);
    if (room && room.connectionCount === 0) {
      this.rooms.delete(id);
    }
  }

  get roomCount(): number {
    return this.rooms.size;
  }
}

/** Multiplayer is opt-in (FP#8 / FR-081 v1). Enabled by the
 * `KICLAUDE_MULTIPLAYER` env flag on the gateway. */
export function multiplayerEnabled(env: Record<string, string | undefined> = process.env): boolean {
  const flag = (env.KICLAUDE_MULTIPLAYER ?? "").toLowerCase();
  return flag === "1" || flag === "true" || flag === "on";
}
