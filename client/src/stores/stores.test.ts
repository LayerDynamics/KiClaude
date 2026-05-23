import { beforeEach, describe, expect, it, vi } from "vitest";

import { useActivityStore } from "./activityStore";
import { useChatStore } from "./chatStore";
import { useKcirStore } from "./kcirStore";
import { useProjectStore } from "./projectStore";
import { useSelectionStore } from "./selectionStore";
import type { KcirProject, KcirFootprintInstance } from "./projectStore";

const sampleProject: KcirProject = {
  kcir_version: "0.1.0",
  name: "blinky",
  pcb: {
    version: 20_240_108,
    generator: "kiclaude",
    layers: [],
    footprints: [],
    tracks: [],
    vias: [],
    zones: [],
    nets: [],
  },
  metadata: { title: "blinky", revision: "0.1", company: "", date: "" },
  net_classes: [],
};

const sampleFootprint: KcirFootprintInstance = {
  uuid: "fp-1",
  refdes: "R1",
  lib_id: "Resistor_SMD:R_0603_1608Metric",
  value: "10k",
  position_mm: [50, 50],
  rotation_deg: 0,
  locked: false,
};

describe("projectStore", () => {
  beforeEach(() => useProjectStore.getState().clear());

  it("setProject transitions to ready", () => {
    useProjectStore.getState().setProject(sampleProject, { projectId: "p-1" });
    const state = useProjectStore.getState();
    expect(state.status).toBe("ready");
    expect(state.project?.name).toBe("blinky");
    expect(state.projectId).toBe("p-1");
  });

  it("setError transitions to error", () => {
    useProjectStore.getState().setError("boom");
    expect(useProjectStore.getState().status).toBe("error");
    expect(useProjectStore.getState().error).toBe("boom");
  });
});

describe("kcirStore", () => {
  beforeEach(() => {
    useKcirStore.setState({ footprints: [], tracks: [], dirty: false });
  });

  it("upsertFootprint inserts and replaces", () => {
    useKcirStore.getState().upsertFootprint(sampleFootprint);
    expect(useKcirStore.getState().footprints).toHaveLength(1);
    useKcirStore.getState().upsertFootprint({ ...sampleFootprint, value: "20k" });
    expect(useKcirStore.getState().footprints).toHaveLength(1);
    expect(useKcirStore.getState().footprints[0]?.value).toBe("20k");
    expect(useKcirStore.getState().dirty).toBe(true);
  });

  it("removeByUuid removes matching footprint", () => {
    useKcirStore.getState().setFootprints([sampleFootprint]);
    useKcirStore.getState().removeByUuid("fp-1");
    expect(useKcirStore.getState().footprints).toHaveLength(0);
  });
});

describe("selectionStore", () => {
  beforeEach(() => useSelectionStore.getState().clear());

  it("toggle adds then removes a ref", () => {
    const ref = { kind: "footprint" as const, uuid: "fp-1" };
    useSelectionStore.getState().toggle(ref);
    expect(useSelectionStore.getState().selected).toHaveLength(1);
    useSelectionStore.getState().toggle(ref);
    expect(useSelectionStore.getState().selected).toHaveLength(0);
  });

  it("select replaces the whole list", () => {
    useSelectionStore.getState().select([
      { kind: "footprint", uuid: "a" },
      { kind: "track", uuid: "b" },
    ]);
    expect(useSelectionStore.getState().selected).toHaveLength(2);
  });
});

describe("activityStore", () => {
  beforeEach(() => useActivityStore.getState().clear());

  it("append respects maxEntries", () => {
    useActivityStore.getState().setMaxEntries(3);
    for (let i = 0; i < 5; i += 1) {
      useActivityStore.getState().append({
        id: `id-${i}`,
        ts: new Date().toISOString(),
        event: "PreToolUse",
        tool_name: `t${i}`,
        mutating: true,
        status: "ok",
      });
    }
    const entries = useActivityStore.getState().entries;
    expect(entries).toHaveLength(3);
    expect(entries.map((e) => e.tool_name)).toEqual(["t2", "t3", "t4"]);
  });

  it("append upserts by id", () => {
    const store = useActivityStore.getState();
    store.append({
      id: "x",
      ts: "2026-01-01T00:00:00.000Z",
      tool_name: "kc_symbol_add",
      mutating: true,
      status: "running",
    });
    store.append({
      id: "x",
      ts: "2026-01-01T00:00:00.001Z",
      tool_name: "kc_symbol_add",
      mutating: true,
      status: "ok",
      duration_ms: 12,
    });
    const entries = useActivityStore.getState().entries;
    expect(entries).toHaveLength(1);
    expect(entries[0]?.status).toBe("ok");
    expect(entries[0]?.duration_ms).toBe(12);
  });
});

describe("chatStore", () => {
  beforeEach(() => useChatStore.getState().clear());

  it("send appends a user message", () => {
    useChatStore.getState().send("hello");
    const msgs = useChatStore.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]?.role).toBe("user");
    expect(msgs[0]?.content).toBe("hello");
  });

  it("appendAssistantToken streams incrementally", () => {
    useChatStore.getState().appendAssistantToken("m1", "hel");
    useChatStore.getState().appendAssistantToken("m1", "lo");
    const msgs = useChatStore.getState().messages;
    expect(msgs).toHaveLength(1);
    expect(msgs[0]?.content).toBe("hello");
    expect(msgs[0]?.streaming).toBe(true);
    useChatStore.getState().finalizeAssistant("m1");
    expect(useChatStore.getState().messages[0]?.streaming).toBe(false);
  });

  it("setStatus updates status + error", () => {
    useChatStore.getState().setStatus("error", "conn failed");
    expect(useChatStore.getState().status).toBe("error");
    expect(useChatStore.getState().error).toBe("conn failed");
  });
});

// Suppress unused-import-only lint in CI by referencing vi at least once
// for any future spy-based extensions in this file.
const _vi = vi;
void _vi;
