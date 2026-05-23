import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ErcPanel, type ErcIssue } from "./ErcPanel";

afterEach(() => cleanup());

function reportResponse(body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function failureResponse(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

const SAMPLE: ErcIssue[] = [
  {
    severity: "warning",
    sheet: "/root",
    position_mm: [10.0, 20.0],
    type: "no_connect",
    description: "unconnected pin",
  },
  {
    severity: "error",
    sheet: "/root/A",
    position_mm: [30.0, 40.0],
    type: "label_dangling",
    description: "dangling label",
  },
];

describe("ErcPanel", () => {
  it("renders the Run ERC button on first mount", () => {
    render(<ErcPanel projectId="p" projectPath="/tmp/p.kicad_sch" />);
    expect(screen.getByTestId("erc-run-button").textContent).toContain("Run ERC");
  });

  it("posts to the connector and renders issues grouped by severity", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      reportResponse({
        ok: true,
        issues: SAMPLE,
        error: null,
        duration_ms: 42,
        exit_code: 0,
      }),
    );
    render(
      <ErcPanel
        projectId="p"
        projectPath="/tmp/p.kicad_sch"
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.click(screen.getByTestId("erc-run-button"));
    await waitFor(() => screen.getByTestId("erc-issue-list"));
    const url = String(fetcher.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/api/connector/tools/erc");
    expect(screen.getByTestId("erc-group-error")).toBeTruthy();
    expect(screen.getByTestId("erc-group-warning")).toBeTruthy();
    expect(screen.getByTestId("erc-summary-error").textContent).toContain("1");
    expect(screen.getByTestId("erc-summary-warning").textContent).toContain("1");
  });

  it("calls onFlyTo with sheet uuid + position when an issue is clicked", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      reportResponse({
        ok: true,
        issues: SAMPLE,
        error: null,
      }),
    );
    const onFlyTo = vi.fn();
    render(
      <ErcPanel
        projectId="p"
        projectPath="/tmp/p.kicad_sch"
        fetcher={fetcher as unknown as typeof fetch}
        onFlyTo={onFlyTo}
      />,
    );
    fireEvent.click(screen.getByTestId("erc-run-button"));
    await waitFor(() => screen.getByTestId("erc-issue-error-0"));
    const btn = screen
      .getByTestId("erc-issue-error-0")
      .querySelector("button") as HTMLButtonElement;
    fireEvent.click(btn);
    expect(onFlyTo).toHaveBeenCalledWith("/root/A", [30.0, 40.0]);
  });

  it("renders an ERC-clean message when issues list is empty", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      reportResponse({ ok: true, issues: [], error: null }),
    );
    render(
      <ErcPanel
        projectId="p"
        projectPath="/tmp/p.kicad_sch"
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.click(screen.getByTestId("erc-run-button"));
    await waitFor(() => screen.getByTestId("erc-clean"));
    expect(screen.getByTestId("erc-clean").textContent).toContain("ERC clean");
  });

  it("surfaces a tool error envelope (ok=false)", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      reportResponse({ ok: false, issues: [], error: "kicad-cli crashed" }),
    );
    render(
      <ErcPanel
        projectId="p"
        projectPath="/tmp/p.kicad_sch"
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.click(screen.getByTestId("erc-run-button"));
    await waitFor(() => screen.getByTestId("erc-tool-error"));
    expect(screen.getByTestId("erc-tool-error").textContent).toContain(
      "kicad-cli crashed",
    );
  });

  it("surfaces 503 (kicad-cli not on PATH) as a transport error", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      failureResponse(503, { detail: "kicad-cli not on PATH" }),
    );
    render(
      <ErcPanel
        projectId="p"
        projectPath="/tmp/p.kicad_sch"
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.click(screen.getByTestId("erc-run-button"));
    await waitFor(() => screen.getByTestId("erc-transport-error"));
    expect(screen.getByTestId("erc-transport-error").textContent).toContain(
      "kicad-cli not on PATH",
    );
  });
});
