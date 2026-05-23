import { afterEach, describe, expect, it } from "vitest";

import { createApp } from "../index.js";
import {
  ApprovalDecision,
  ApprovalDispatcher,
  setDispatcher,
} from "./approval.js";

afterEach(() => {
  setDispatcher(null);
});

describe("/api/approval/request", () => {
  it("returns the dispatcher's decision verbatim", async () => {
    const dispatcher: ApprovalDispatcher = {
      async request(prompt) {
        expect(prompt.tool_name).toBe("kc_symbol_add");
        expect(prompt.tool_input).toEqual({ refdes: "R1" });
        return "allow";
      },
    };
    setDispatcher(dispatcher);
    const app = createApp();
    const res = await app.request("/api/approval/request", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        tool_name: "kc_symbol_add",
        tool_input: { refdes: "R1" },
        session_id: "s1",
        project_id: "p1",
      }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      ok: boolean;
      decision: ApprovalDecision;
    };
    expect(body.ok).toBe(true);
    expect(body.decision).toBe("allow");
  });

  it("falls back to deny when no UI is connected (default)", async () => {
    const app = createApp();
    const res = await app.request("/api/approval/request", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        tool_name: "kc_wire_connect",
        session_id: "s1",
      }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as { decision: ApprovalDecision };
    expect(body.decision).toBe("deny");
  });

  it("rejects requests missing tool_name with 400", async () => {
    const app = createApp();
    const res = await app.request("/api/approval/request", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
  });

  it("times out and returns deny when the dispatcher takes too long", async () => {
    const dispatcher: ApprovalDispatcher = {
      request(_prompt, signal) {
        return new Promise<ApprovalDecision>((resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")));
        });
      },
    };
    setDispatcher(dispatcher);
    const app = createApp();
    const res = await app.request("/api/approval/request", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        tool_name: "kc_symbol_add",
        timeout_ms: 50,
      }),
    });
    expect(res.status).toBe(200);
    const body = (await res.json()) as {
      decision: ApprovalDecision;
      error?: string;
    };
    expect(body.decision).toBe("deny");
    expect(body.error).toBe("timeout");
  });

  it("rejects an invalid JSON body with 400", async () => {
    const app = createApp();
    const res = await app.request("/api/approval/request", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: "{not-json",
    });
    expect(res.status).toBe(400);
  });
});
