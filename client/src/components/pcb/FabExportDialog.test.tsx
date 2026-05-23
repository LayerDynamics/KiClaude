import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FabExportDialog,
  useFabExportDialog,
} from "./FabExportDialog";

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function makeFetcher(handler: (url: string, init?: RequestInit) => unknown) {
  return vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const body = handler(url, init);
    if (body instanceof Response) return Promise.resolve(body);
    return Promise.resolve(mockResponse(body));
  });
}

describe("useFabExportDialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("runDfm GETs the kiserver DFM endpoint with the chosen target", async () => {
    const fetcher = makeFetcher((url) => {
      expect(url).toContain("/api/server/project/p1/dfm/check?target=jlcpcb");
      return {
        ok: true,
        target: "jlcpcb",
        issues: [],
        counts: { error: 0, warning: 0 },
      };
    });
    const { result } = renderHook(() =>
      useFabExportDialog({
        projectId: "p1",
        pcbPath: "/tmp/b.kicad_pcb",
        fetcher,
      }),
    );
    act(() => result.current.setTarget("jlcpcb"));
    await act(async () => {
      await result.current.runDfm();
    });
    expect(result.current.dfm?.ok).toBe(true);
  });

  it("setTarget drops a stale DFM result so the export gate re-arms", async () => {
    const fetcher = makeFetcher(() => ({
      ok: true,
      target: "jlcpcb",
      issues: [],
      counts: { error: 0, warning: 0 },
    }));
    const { result } = renderHook(() =>
      useFabExportDialog({
        projectId: "p1",
        pcbPath: "/tmp/b.kicad_pcb",
        fetcher,
      }),
    );
    await act(async () => {
      await result.current.runDfm();
    });
    expect(result.current.dfm).not.toBeNull();
    act(() => result.current.setTarget("oshpark"));
    expect(result.current.dfm).toBeNull();
  });

  it("exportBundle refuses when DFM has errors", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: false,
        target: "jlcpcb",
        issues: [
          {
            severity: "error",
            rule: "min_track",
            description: "0.05 mm track",
            items: ["F.Cu", "track:t1"],
            actual_mm: 0.05,
            limit_mm: 0.127,
          },
        ],
        counts: { error: 1, warning: 0 },
      }),
    );
    const { result } = renderHook(() =>
      useFabExportDialog({
        projectId: "p1",
        pcbPath: "/tmp/b.kicad_pcb",
        fetcher,
      }),
    );
    await act(async () => {
      await result.current.runDfm();
    });
    let exported;
    await act(async () => {
      exported = await result.current.exportBundle();
    });
    expect(exported).toBeNull();
    expect(result.current.error).toMatch(/DFM errors/);
    // Only the DFM call hit the network — no export POSTs.
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("exportBundle POSTs to four kiconnector endpoints when DFM passes", async () => {
    const calls: string[] = [];
    const fetcher = vi
      .fn()
      .mockImplementation((url: string) => {
        calls.push(url);
        if (url.includes("dfm/check")) {
          return Promise.resolve(
            mockResponse({
              ok: true,
              target: "jlcpcb",
              issues: [
                {
                  severity: "warning",
                  rule: "advise_track",
                  description: "0.15 mm",
                  items: ["F.Cu"],
                  actual_mm: 0.15,
                  limit_mm: 0.2,
                },
              ],
              counts: { error: 0, warning: 1 },
            }),
          );
        }
        if (url.endsWith("/tools/gerbers")) {
          return Promise.resolve(
            mockResponse({
              ok: true,
              files: ["a.gbr", "b.gbr"],
              output_dir: "fab",
            }),
          );
        }
        if (url.endsWith("/tools/drill")) {
          return Promise.resolve(
            mockResponse({ ok: true, files: ["a.drl"] }),
          );
        }
        if (url.endsWith("/tools/pos")) {
          return Promise.resolve(
            mockResponse({ ok: true, files: ["pos-both.csv"] }),
          );
        }
        if (url.endsWith("/tools/bom")) {
          return Promise.resolve(mockResponse({ ok: true, files: ["bom.csv"] }));
        }
        return Promise.resolve(mockResponse({ ok: false, error: "?" }));
      });
    const cb = vi.fn();
    const { result } = renderHook(() =>
      useFabExportDialog({
        projectId: "p1",
        pcbPath: "/tmp/b.kicad_pcb",
        schPath: "/tmp/b.kicad_sch",
        fetcher,
        onExported: cb,
      }),
    );
    act(() => result.current.setTarget("jlcpcb"));
    await act(async () => {
      await result.current.runDfm();
    });
    await act(async () => {
      await result.current.exportBundle();
    });
    expect(cb).toHaveBeenCalledTimes(1);
    expect(result.current.exportResult?.ok).toBe(true);
    expect(result.current.exportResult?.artifacts.bom.ok).toBe(true);
    // 1 DFM + 4 fab POSTs.
    expect(fetcher).toHaveBeenCalledTimes(5);
    expect(calls.filter((u) => u.includes("/tools/"))).toHaveLength(4);
  });

  it("schPath omitted → bom artifact reports skipped", async () => {
    const fetcher = vi
      .fn()
      .mockImplementation((url: string) => {
        if (url.includes("dfm/check")) {
          return Promise.resolve(
            mockResponse({
              ok: true,
              target: "jlcpcb",
              issues: [],
              counts: { error: 0, warning: 0 },
            }),
          );
        }
        return Promise.resolve(mockResponse({ ok: true, files: [] }));
      });
    const { result } = renderHook(() =>
      useFabExportDialog({
        projectId: "p1",
        pcbPath: "/tmp/b.kicad_pcb",
        fetcher,
      }),
    );
    await act(async () => {
      await result.current.runDfm();
    });
    await act(async () => {
      await result.current.exportBundle();
    });
    expect(result.current.exportResult?.artifacts.bom.skipped).toBe(true);
  });
});

describe("FabExportDialog", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders the target selector + DFM button + disabled Export button until DFM runs", () => {
    const fetcher = vi.fn();
    render(
      <FabExportDialog
        projectId="p1"
        pcbPath="/tmp/b.kicad_pcb"
        fetcher={fetcher}
      />,
    );
    const exportBtn = screen.getByTestId("fab-export") as HTMLButtonElement;
    expect(exportBtn.disabled).toBe(true);
    const select = screen.getByTestId("fab-target-select") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "jlcpcb",
      "oshpark",
      "pcbway",
      "generic",
    ]);
  });

  it("running DFM with errors keeps Export disabled and lists issues", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: false,
        target: "jlcpcb",
        issues: [
          {
            severity: "error",
            rule: "min_track",
            description: "0.05 mm track",
            items: ["F.Cu", "track:t1"],
            actual_mm: 0.05,
            limit_mm: 0.127,
          },
        ],
        counts: { error: 1, warning: 0 },
      }),
    );
    render(
      <FabExportDialog
        projectId="p1"
        pcbPath="/tmp/b.kicad_pcb"
        fetcher={fetcher}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("fab-dfm-run"));
    });
    await waitFor(() =>
      expect(screen.getAllByTestId("fab-dfm-issue")).toHaveLength(1),
    );
    const exportBtn = screen.getByTestId("fab-export") as HTMLButtonElement;
    expect(exportBtn.disabled).toBe(true);
  });

  it("clean DFM enables Export; clicking it shows the per-artifact summary", async () => {
    const fetcher = vi
      .fn()
      .mockImplementation((url: string) => {
        if (url.includes("dfm/check")) {
          return Promise.resolve(
            mockResponse({
              ok: true,
              target: "jlcpcb",
              issues: [],
              counts: { error: 0, warning: 0 },
            }),
          );
        }
        return Promise.resolve(mockResponse({ ok: true, files: ["x"] }));
      });
    render(
      <FabExportDialog
        projectId="p1"
        pcbPath="/tmp/b.kicad_pcb"
        schPath="/tmp/b.kicad_sch"
        fetcher={fetcher}
      />,
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("fab-dfm-run"));
    });
    await waitFor(() =>
      expect(
        (screen.getByTestId("fab-export") as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId("fab-export"));
    });
    await waitFor(() => screen.getByTestId("fab-export-summary"));
    expect(screen.getAllByTestId("fab-artifact-row")).toHaveLength(4);
  });
});
