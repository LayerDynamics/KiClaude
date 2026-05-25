import { describe, expect, it } from "vitest";
import * as Y from "yjs";

import {
  CrdtHub,
  CrdtRoom,
  type CrdtMessage,
  decodeU8,
  encodeU8,
  multiplayerEnabled,
  PROJECT_MAP,
} from "./crdt.js";
import { parseCrdtMessage } from "./ws.js";

describe("CrdtRoom sync (FR-081)", () => {
  it("a joining client pulls the authoritative doc state", () => {
    const room = new CrdtRoom("p1");
    room.doc.getMap(PROJECT_MAP).set("name", "esp32_c6_rf");

    // Client is empty; it sends its state vector...
    const client = new Y.Doc();
    const out = room.process({ kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(client)) });

    const step2 = out.find((o) => o.message.kind === "sync-step2");
    expect(step2).toBeDefined();
    // ...applies the diff and converges to the server's state.
    Y.applyUpdate(client, decodeU8((step2!.message as { update: string }).update));
    expect(client.getMap(PROJECT_MAP).get("name")).toBe("esp32_c6_rf");

    // The reply also re-offers the server SV so the client reciprocates.
    expect(out.some((o) => o.message.kind === "sync-step1")).toBe(true);
  });

  it("an inbound update is applied to the authoritative doc and fanned to others", () => {
    const room = new CrdtRoom("p1");
    const client = new Y.Doc();
    client.getMap(PROJECT_MAP).set("layers", 4);

    const update = Y.encodeStateAsUpdate(client);
    const out = room.process({ kind: "update", update: encodeU8(update) });

    expect(room.doc.getMap(PROJECT_MAP).get("layers")).toBe(4);
    expect(out).toEqual([
      { to: "others", message: { kind: "update", update: encodeU8(update) } },
    ]);
  });

  it("dispatch routes 'others' to every peer except the origin", () => {
    const room = new CrdtRoom("p1");
    const a: CrdtMessage[] = [];
    const b: CrdtMessage[] = [];
    const sendA = (m: CrdtMessage) => a.push(m);
    const sendB = (m: CrdtMessage) => b.push(m);
    room.join(sendA);
    room.join(sendB);

    const update = encodeU8(Y.encodeStateAsUpdate((() => {
      const d = new Y.Doc();
      d.getMap(PROJECT_MAP).set("k", 1);
      return d;
    })()));
    const out = room.process({ kind: "update", update });
    room.dispatch(out, sendA);

    expect(a).toEqual([]); // origin does not get its own update back
    expect(b).toEqual([{ kind: "update", update }]); // peer does
  });

  it("two clients converge through the room", () => {
    const room = new CrdtRoom("p1");
    const alice = new Y.Doc();
    const bob = new Y.Doc();

    // Both connect and pull current (empty) state — trivially converged.
    room.process({ kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(alice)) });
    room.process({ kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(bob)) });

    // Alice edits; her update flows to the room and on to Bob.
    alice.getMap(PROJECT_MAP).set("net", "GND");
    const aliceUpdate = encodeU8(Y.encodeStateAsUpdate(alice));
    const fan = room.process({ kind: "update", update: aliceUpdate });
    expect(fan[0].to).toBe("others");
    Y.applyUpdate(bob, decodeU8((fan[0].message as { update: string }).update));

    expect(room.doc.getMap(PROJECT_MAP).get("net")).toBe("GND");
    expect(bob.getMap(PROJECT_MAP).get("net")).toBe("GND");
  });

  it("greeting offers the server state vector", () => {
    const room = new CrdtRoom("p1");
    room.doc.getMap(PROJECT_MAP).set("x", 1);
    const greet = room.greeting();
    expect(greet).toHaveLength(1);
    expect(greet[0]).toEqual({
      to: "sender",
      message: { kind: "sync-step1", sv: encodeU8(Y.encodeStateVector(room.doc)) },
    });
  });

  it("tracks connection count and unsubscribes", () => {
    const room = new CrdtRoom("p1");
    const off = room.join(() => {});
    expect(room.connectionCount).toBe(1);
    off();
    expect(room.connectionCount).toBe(0);
  });
});

describe("CrdtHub", () => {
  it("reuses a room by id and releases it when empty", () => {
    const hub = new CrdtHub();
    const r1 = hub.room("p1");
    const r2 = hub.room("p1");
    expect(r1).toBe(r2);
    expect(hub.roomCount).toBe(1);
    hub.release("p1"); // no connections → dropped
    expect(hub.roomCount).toBe(0);
  });

  it("keeps a room with live connections", () => {
    const hub = new CrdtHub();
    const room = hub.room("p1");
    room.join(() => {});
    hub.release("p1");
    expect(hub.roomCount).toBe(1);
  });
});

describe("multiplayerEnabled", () => {
  it("is off by default and on for truthy flags", () => {
    expect(multiplayerEnabled({})).toBe(false);
    expect(multiplayerEnabled({ KICLAUDE_MULTIPLAYER: "0" })).toBe(false);
    expect(multiplayerEnabled({ KICLAUDE_MULTIPLAYER: "1" })).toBe(true);
    expect(multiplayerEnabled({ KICLAUDE_MULTIPLAYER: "true" })).toBe(true);
    expect(multiplayerEnabled({ KICLAUDE_MULTIPLAYER: "on" })).toBe(true);
  });
});

// parseCrdtMessage lives in ws.ts (the route boundary) but is tested
// here alongside the protocol it guards.
describe("parseCrdtMessage", () => {
  it("accepts the three valid envelopes", () => {
    expect(parseCrdtMessage(JSON.stringify({ kind: "sync-step1", sv: "AA==" }))).toEqual({
      kind: "sync-step1",
      sv: "AA==",
    });
    expect(parseCrdtMessage(JSON.stringify({ kind: "sync-step2", update: "BB==" }))).toEqual({
      kind: "sync-step2",
      update: "BB==",
    });
    expect(parseCrdtMessage(JSON.stringify({ kind: "update", update: "CC==" }))).toEqual({
      kind: "update",
      update: "CC==",
    });
  });

  it("rejects malformed / hostile frames", () => {
    expect(parseCrdtMessage("not json")).toBeNull();
    expect(parseCrdtMessage(JSON.stringify({ kind: "sync-step1" }))).toBeNull(); // missing sv
    expect(parseCrdtMessage(JSON.stringify({ kind: "evil", update: "x" }))).toBeNull();
    expect(parseCrdtMessage(JSON.stringify({ kind: "update", update: 42 }))).toBeNull();
    expect(parseCrdtMessage(123)).toBeNull();
    expect(parseCrdtMessage(JSON.stringify(["array"]))).toBeNull();
  });
});
