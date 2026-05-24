import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SubagentActivityPanel,
  buildSessionTree,
  type ActivityCallRecord,
  type ActivitySessionRecord,
} from "./SubagentActivityPanel";

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const ORCH_SESSION: ActivitySessionRecord = {
  session_id: "orch-1234",
  agent_id: "",
  parent_session_id: null,
  started_at: "2026-05-24T00:00:00.000Z",
  ended_at: null,
  seq: 1,
};
const DECOUPLING_SESSION: ActivitySessionRecord = {
  session_id: "dec-1",
  agent_id: "decoupling-auditor",
  parent_session_id: "orch-1234",
  started_at: "2026-05-24T00:00:01.000Z",
  ended_at: null,
  seq: 2,
};
const BOM_SESSION: ActivitySessionRecord = {
  session_id: "bom-1",
  agent_id: "bom-sourcer",
  parent_session_id: "orch-1234",
  started_at: "2026-05-24T00:00:02.000Z",
  ended_at: "2026-05-24T00:00:05.000Z",
  seq: 3,
};
const ORCH_CALL: ActivityCallRecord = {
  tool_use_id: "call-orch-1",
  session_id: "orch-1234",
  tool_name: "kc_kcir_get",
  project_id: "blinky",
  started_at: "2026-05-24T00:00:00.500Z",
  ended_at: "2026-05-24T00:00:00.700Z",
  ok: true,
  duration_ms: 200,
  status: "ok",
  seq: 4,
};
const DECOUPLING_CALL: ActivityCallRecord = {
  tool_use_id: "call-dec-1",
  session_id: "dec-1",
  tool_name: "kc_kcir_get",
  project_id: "blinky",
  started_at: "2026-05-24T00:00:01.500Z",
  ended_at: null,
  ok: null,
  duration_ms: null,
  status: "running",
  seq: 5,
};
const BOM_CALL: ActivityCallRecord = {
  tool_use_id: "call-bom-1",
  session_id: "bom-1",
  tool_name: "kc_bom_price",
  project_id: "blinky",
  started_at: "2026-05-24T00:00:02.500Z",
  ended_at: "2026-05-24T00:00:04.500Z",
  ok: false,
  duration_ms: 2000,
  status: "error",
  seq: 6,
};

describe("buildSessionTree (M3-T-09)", () => {
  it("nests subagent sessions under their parent", () => {
    const tree = buildSessionTree(
      [ORCH_SESSION, DECOUPLING_SESSION, BOM_SESSION],
      [],
    );
    expect(tree).toHaveLength(1);
    expect(tree[0]!.record.session_id).toBe("orch-1234");
    const childIds = tree[0]!.children.map((c) => c.record.session_id);
    expect(childIds).toEqual(["dec-1", "bom-1"]);
  });

  it("attaches calls to their owning sessions", () => {
    const tree = buildSessionTree(
      [ORCH_SESSION, DECOUPLING_SESSION],
      [ORCH_CALL, DECOUPLING_CALL],
    );
    expect(tree[0]!.calls).toHaveLength(1);
    expect(tree[0]!.calls[0]!.tool_use_id).toBe("call-orch-1");
    expect(tree[0]!.children[0]!.calls).toHaveLength(1);
    expect(tree[0]!.children[0]!.calls[0]!.tool_use_id).toBe("call-dec-1");
  });

  it("treats parent_session_id referencing an unknown id as a root", () => {
    // Subagent whose parent isn't in the snapshot (yet) — still
    // render at root so the user sees the call.
    const orphan: ActivitySessionRecord = {
      ...DECOUPLING_SESSION,
      parent_session_id: "never-saw-this-parent",
    };
    const tree = buildSessionTree([orphan], []);
    expect(tree).toHaveLength(1);
    expect(tree[0]!.record.session_id).toBe("dec-1");
  });
});

describe("SubagentActivityPanel (M3-T-09)", () => {
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("polls /activity/snapshot on mount and renders the empty state", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(mockResponse({ ok: true, sessions: [], calls: [], high_water_seq: 0 }));
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={0} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    expect(fetcher.mock.calls[0]![0]).toBe("/api/agent/activity/snapshot");
    expect(screen.queryByTestId("subagent-activity-empty")).not.toBeNull();
  });

  it("renders orchestrator + nested subagent cards from snapshot data", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        sessions: [ORCH_SESSION, DECOUPLING_SESSION, BOM_SESSION],
        calls: [ORCH_CALL, DECOUPLING_CALL, BOM_CALL],
        high_water_seq: 6,
      }),
    );
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session").length).toBeGreaterThan(0));
    const sessions = screen.getAllByTestId("subagent-session");
    expect(sessions).toHaveLength(3);
    const orchCard = sessions.find((s) => s.dataset.sessionId === "orch-1234")!;
    expect(orchCard.dataset.depth).toBe("0");
    expect(orchCard.dataset.agentId).toBe("orchestrator");
    expect(orchCard.dataset.live).toBe("true");
    const decCard = sessions.find((s) => s.dataset.sessionId === "dec-1")!;
    expect(decCard.dataset.depth).toBe("1");
    expect(decCard.dataset.agentId).toBe("decoupling-auditor");
    expect(decCard.dataset.parentSessionId).toBe("orch-1234");
    const bomCard = sessions.find((s) => s.dataset.sessionId === "bom-1")!;
    expect(bomCard.dataset.live).toBe("false");
  });

  it("renders one call row per tool call with the right status", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        sessions: [ORCH_SESSION, DECOUPLING_SESSION, BOM_SESSION],
        calls: [ORCH_CALL, DECOUPLING_CALL, BOM_CALL],
        high_water_seq: 6,
      }),
    );
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.queryAllByTestId("subagent-call").length).toBeGreaterThan(0));
    const calls = screen.getAllByTestId("subagent-call");
    expect(calls).toHaveLength(3);
    const byId = Object.fromEntries(calls.map((c) => [c.dataset.toolUseId, c.dataset.status]));
    expect(byId["call-orch-1"]).toBe("ok");
    expect(byId["call-dec-1"]).toBe("running");
    expect(byId["call-bom-1"]).toBe("error");
  });

  it("summary header counts sessions / calls / active / running / errors", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        sessions: [ORCH_SESSION, DECOUPLING_SESSION, BOM_SESSION],
        calls: [ORCH_CALL, DECOUPLING_CALL, BOM_CALL],
        high_water_seq: 6,
      }),
    );
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session").length).toBe(3));
    const summary = screen.getByTestId("subagent-activity-summary").textContent ?? "";
    expect(summary).toContain("3 sessions");
    expect(summary).toContain("3 calls");
    expect(summary).toContain("2 active"); // orch + dec are live; bom ended.
    expect(summary).toContain("1 running"); // call-dec-1
    expect(summary).toContain("1 errored"); // call-bom-1
  });

  it("subsequent polls use `since=high_water_seq` and merge incremental rows", async () => {
    // Real timers with a tight interval; advanceTimersByTimeAsync
    // doesn't play with @testing-library/react's waitFor (which
    // polls on real time), so we just sleep instead.
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        mockResponse({
          ok: true,
          sessions: [ORCH_SESSION],
          calls: [ORCH_CALL],
          high_water_seq: 4,
        }),
      )
      .mockResolvedValueOnce(
        mockResponse({
          ok: true,
          sessions: [DECOUPLING_SESSION],
          calls: [DECOUPLING_CALL],
          high_water_seq: 5,
        }),
      );
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={50} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session").length).toBe(1));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(fetcher.mock.calls[1]![0]).toBe("/api/agent/activity/snapshot?since=4");
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session").length).toBe(2));
  });

  it("surfaces server errors in a banner without losing existing rows", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        mockResponse({
          ok: true,
          sessions: [ORCH_SESSION],
          calls: [ORCH_CALL],
          high_water_seq: 4,
        }),
      )
      .mockResolvedValueOnce(mockResponse({ ok: false, error: "agent unreachable" }, 502));
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={50} />);
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session").length).toBe(1));
    await waitFor(() => expect(screen.queryByTestId("subagent-activity-error")).not.toBeNull(), {
      timeout: 2000,
    });
    expect(screen.getByTestId("subagent-activity-error").textContent ?? "").toContain(
      "agent unreachable",
    );
    expect(screen.queryAllByTestId("subagent-session")).toHaveLength(1);
  });

  it("Clear DELETEs /activity and resets local state", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        mockResponse({
          ok: true,
          sessions: [ORCH_SESSION],
          calls: [ORCH_CALL],
          high_water_seq: 4,
        }),
      )
      .mockResolvedValueOnce(mockResponse({ ok: true }));
    render(<SubagentActivityPanel fetcher={fetcher} pollIntervalMs={0} />);
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session").length).toBe(1));
    fireEvent.click(screen.getByTestId("subagent-activity-clear"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    expect(fetcher.mock.calls[1]![0]).toBe("/api/agent/activity");
    expect((fetcher.mock.calls[1]![1] as RequestInit).method).toBe("DELETE");
    await waitFor(() => expect(screen.queryAllByTestId("subagent-session")).toHaveLength(0));
    expect(screen.queryByTestId("subagent-activity-empty")).not.toBeNull();
  });
});
