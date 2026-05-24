import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useProjectStore, type KcirProject } from "../../stores/projectStore";

import { BomView, type BomPricing, type BomLine } from "./BomView";

function mockResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function buildProject(): KcirProject {
  return {
    kcir_version: "0.4",
    name: "blinky",
    metadata: { title: "", revision: "", company: "", date: "" },
    net_classes: [],
    pcb: {
      version: 1,
      generator: "kiclaude",
      layers: [{ id: 0, name: "F.Cu", kind: "copper" }],
      footprints: [],
      tracks: [],
      vias: [],
      zones: [],
      nets: [],
    },
  };
}

const TWO_LINE_BOM: { bom_lines: BomLine[]; pricing: BomPricing } = {
  bom_lines: [
    { mpn: "STM32F103C8T6", qty: 1, refdes_count: 1 },
    { mpn: "GRM188R71H104KA93D", qty: 12, refdes_count: 12 },
  ],
  pricing: {
    parts: [
      {
        mpn: "STM32F103C8T6",
        requested_qty: 1,
        cheapest: {
          distributor: "digikey",
          distributor_sku: "497-6063-ND",
          manufacturer: "STMicroelectronics",
          description: "ARM Cortex-M3 MCU",
          in_stock_qty: 420,
          lifecycle: "active",
          product_url: "https://digikey.com/p/497-6063-ND",
          unit_price_usd: 2.5,
        },
        line_total_usd: 2.5,
        errors: {},
        quote_count: 2,
      },
      {
        mpn: "GRM188R71H104KA93D",
        requested_qty: 12,
        cheapest: {
          distributor: "digikey",
          distributor_sku: "490-1532-1-ND",
          manufacturer: "Murata",
          description: "100nF 0603 X7R",
          in_stock_qty: 100000,
          lifecycle: "active",
          product_url: "https://digikey.com/p/490-1532-1-ND",
          unit_price_usd: 0.10,
        },
        line_total_usd: 1.20,
        errors: {},
        quote_count: 1,
      },
    ],
    distributor_totals_usd: { digikey: 3.70 },
    grand_total_usd: 3.70,
    missing_mpns: [],
    errors: {},
  },
};

describe("BomView (M3-T-08)", () => {
  beforeEach(() => {
    act(() => {
      useProjectStore.getState().clear();
    });
  });
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it("renders an empty banner when no project is loaded", () => {
    render(<BomView projectId="p1" fetcher={vi.fn()} />);
    expect(screen.getByTestId("bom-view").dataset.status).toBe("empty");
  });

  it("fetches BOM pricing on mount and renders one row per MPN", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(mockResponse({ ok: true, ...TWO_LINE_BOM }));
    render(<BomView projectId="proj-1" fetcher={fetcher} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    const url = fetcher.mock.calls[0]![0] as string;
    expect(url).toBe("/api/server/project/proj-1/bom/price?qty_multiplier=1");
    await waitFor(() => expect(screen.queryAllByTestId("bom-row").length).toBe(2));
    const rows = screen.getAllByTestId("bom-row");
    expect(rows.map((r) => r.dataset.mpn)).toEqual([
      "STM32F103C8T6",
      "GRM188R71H104KA93D",
    ]);
    expect(screen.getByTestId("bom-grand-total").textContent).toMatch(/\$3\.70/);
    expect(screen.getByTestId("bom-cart-split").textContent).toMatch(/digikey \$3\.70/);
  });

  it("renders the cheapest distributor as a click-through link to the product URL", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(mockResponse({ ok: true, ...TWO_LINE_BOM }));
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.queryAllByTestId("bom-distributor-link").length).toBe(2));
    const links = screen.getAllByTestId("bom-distributor-link");
    expect((links[0] as HTMLAnchorElement).href).toBe("https://digikey.com/p/497-6063-ND");
    expect((links[0] as HTMLAnchorElement).target).toBe("_blank");
    // rel includes both noopener and noreferrer so we don't leak referer.
    const rel = (links[0] as HTMLAnchorElement).rel;
    expect(rel).toContain("noopener");
    expect(rel).toContain("noreferrer");
  });

  it("Refresh re-fetches with force_refresh=true and the current qty multiplier", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(mockResponse({ ok: true, ...TWO_LINE_BOM }));
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByTestId("bom-qty"), { target: { value: "100" } });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    fireEvent.click(screen.getByTestId("bom-refresh"));
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(3));
    const lastUrl = fetcher.mock.calls.at(-1)![0] as string;
    expect(lastUrl).toContain("qty_multiplier=100");
    expect(lastUrl).toContain("force_refresh=true");
  });

  it("changing qty_multiplier re-fetches without force_refresh", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(mockResponse({ ok: true, ...TWO_LINE_BOM }));
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1));
    fireEvent.change(screen.getByTestId("bom-qty"), { target: { value: "10" } });
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    const secondUrl = fetcher.mock.calls[1]![0] as string;
    expect(secondUrl).toContain("qty_multiplier=10");
    expect(secondUrl).not.toContain("force_refresh");
  });

  it("clamps qty multiplier to >= 1", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(mockResponse({ ok: true, ...TWO_LINE_BOM }));
    render(<BomView projectId="p1" fetcher={fetcher} />);
    fireEvent.change(screen.getByTestId("bom-qty"), { target: { value: "0" } });
    fireEvent.change(screen.getByTestId("bom-qty"), { target: { value: "-5" } });
    expect((screen.getByTestId("bom-qty") as HTMLInputElement).value).toBe("1");
  });

  it("surfaces server errors in a banner and keeps rows from any prior load", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(mockResponse({ ok: true, ...TWO_LINE_BOM }))
      .mockResolvedValueOnce(mockResponse({ ok: false, error: "agent unreachable" }, 502));
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.queryAllByTestId("bom-row").length).toBe(2));
    fireEvent.click(screen.getByTestId("bom-refresh"));
    await waitFor(() => expect(screen.queryByTestId("bom-error")).not.toBeNull());
    expect(screen.getByTestId("bom-error").textContent ?? "").toContain("agent unreachable");
    expect(screen.queryAllByTestId("bom-row")).toHaveLength(2);
  });

  it("flags missing MPNs in a warn row", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        bom_lines: [{ mpn: "MISS-1", qty: 1, refdes_count: 1 }],
        pricing: {
          parts: [
            {
              mpn: "MISS-1",
              requested_qty: 1,
              cheapest: null,
              line_total_usd: null,
              errors: {},
              quote_count: 0,
            },
          ],
          distributor_totals_usd: {},
          grand_total_usd: 0,
          missing_mpns: ["MISS-1"],
          errors: {},
        },
      }),
    );
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.queryByTestId("bom-missing")).not.toBeNull());
    expect(screen.getByTestId("bom-missing").textContent ?? "").toContain("MISS-1");
    expect(screen.queryByTestId("bom-no-quote")).not.toBeNull();
  });

  it("renders per-distributor errors in a warn row", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        bom_lines: [],
        pricing: {
          parts: [],
          distributor_totals_usd: {},
          grand_total_usd: 0,
          missing_mpns: [],
          errors: {
            digikey: ["auth: Digi-Key credentials missing"],
          },
        },
      }),
    );
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.queryByTestId("bom-distributor-errors")).not.toBeNull());
    const text = screen.getByTestId("bom-distributor-errors").textContent ?? "";
    expect(text).toContain("digikey");
    expect(text).toContain("credentials missing");
  });

  it("shows the empty-board hint when no MPNs are on the project", async () => {
    act(() => {
      useProjectStore.getState().setProject(buildProject());
    });
    const fetcher = vi.fn().mockResolvedValue(
      mockResponse({
        ok: true,
        bom_lines: [],
        pricing: {
          parts: [], distributor_totals_usd: {}, grand_total_usd: 0,
          missing_mpns: [], errors: {},
        },
      }),
    );
    render(<BomView projectId="p1" fetcher={fetcher} />);
    await waitFor(() => expect(screen.getByTestId("bom-view").dataset.status).toBe("ready"));
    const summary = screen.getByTestId("bom-summary").textContent ?? "";
    expect(summary).toContain("0 unique MPN");
    expect(summary).toContain("Grand total: $0.00");
  });
});
