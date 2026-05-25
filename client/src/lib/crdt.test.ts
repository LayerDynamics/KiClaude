import { describe, expect, it } from "vitest";

import {
  type CrdtMessage,
  type CrdtTransport,
  CrdtSession,
  decodeU8,
  encodeU8,
  PROJECT_MAP,
} from "./crdt";

type Handler = (m: CrdtMessage) => void;

/**
 * A synchronous two-peer relay: A's sends reach B's handler and vice
 * versa. Messages emitted before the peer registers its handler are
 * buffered, so construction order doesn't drop the initial sync.
 */
function transportPair(): [CrdtTransport, CrdtTransport] {
  let hA: Handler | null = null;
  let hB: Handler | null = null;
  const qForA: CrdtMessage[] = [];
  const qForB: CrdtMessage[] = [];
  const toB = (m: CrdtMessage) => (hB ? hB(m) : qForB.push(m));
  const toA = (m: CrdtMessage) => (hA ? hA(m) : qForA.push(m));
  const tA: CrdtTransport = {
    send: toB,
    onMessage: (h) => {
      hA = h;
      qForA.splice(0).forEach(h);
    },
    close: () => {},
  };
  const tB: CrdtTransport = {
    send: toA,
    onMessage: (h) => {
      hB = h;
      qForB.splice(0).forEach(h);
    },
    close: () => {},
  };
  return [tA, tB];
}

describe("CrdtSession (FR-081)", () => {
  it("round-trips binary through the base64 helpers", () => {
    const u8 = new Uint8Array([0, 1, 2, 250, 255]);
    expect(decodeU8(encodeU8(u8))).toEqual(u8);
  });

  it("two peers converge: an edit on one appears on the other", () => {
    const [tA, tB] = transportPair();
    const a = new CrdtSession(tA);
    const b = new CrdtSession(tB);

    a.project.set("name", "esp32_c6_rf");
    expect(b.project.get("name")).toBe("esp32_c6_rf");

    b.project.set("layers", 4);
    expect(a.project.get("layers")).toBe(4);
  });

  it("syncs pre-existing state to a late joiner", () => {
    // A starts with content before B connects.
    const [tA, tB] = transportPair();
    const a = new CrdtSession(tA);
    a.project.set("net", "GND");

    const b = new CrdtSession(tB);
    // The handshake (B's sync-step1 → A's sync-step2) brings B up to date.
    expect(b.project.get("net")).toBe("GND");
  });

  it("does not echo a remote update back to its origin (no loop)", () => {
    const [tA, tB] = transportPair();
    const a = new CrdtSession(tA);
    const b = new CrdtSession(tB);

    let aSends = 0;
    const origSend = tA.send;
    tA.send = (m) => {
      aSends += 1;
      origSend(m);
    };

    b.project.set("k", 1); // change originates on B
    expect(a.project.get("k")).toBe(1);
    // A applied the remote update but must not have re-broadcast it.
    expect(aSends).toBe(0);
  });

  it("stops syncing after close", () => {
    const [tA, tB] = transportPair();
    const a = new CrdtSession(tA);
    const b = new CrdtSession(tB);
    a.project.set("x", 1);
    expect(b.project.get("x")).toBe(1);

    b.close();
    a.project.set("y", 2);
    // B is closed; it must not keep applying updates.
    expect(b.project.get("y")).toBeUndefined();
  });

  it("exposes the shared project map by the agreed name", () => {
    const [tA] = transportPair();
    const a = new CrdtSession(tA);
    expect(a.project).toBe(a.doc.getMap(PROJECT_MAP));
  });
});
