import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PropertyPanel, type SymbolForPanel } from "./PropertyPanel";

const SAMPLE: SymbolForPanel = {
  uuid: "u-1",
  refdes: "R1",
  value: "10k",
  lib_id: "Device:R",
  footprint: "Resistor_SMD:R_0603_1608Metric",
  mpn: "",
  datasheet: "",
};

afterEach(() => cleanup());

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function err(body: unknown, status = 400): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("PropertyPanel", () => {
  it("renders nothing when no symbol is selected", () => {
    const { container } = render(
      <PropertyPanel symbol={null} projectId="p-1" />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("pre-fills every field from the selected symbol", () => {
    render(<PropertyPanel symbol={SAMPLE} projectId="p-1" />);
    expect((screen.getByTestId("property-refdes") as HTMLInputElement).value).toBe(
      "R1",
    );
    expect((screen.getByTestId("property-value") as HTMLInputElement).value).toBe(
      "10k",
    );
    expect((screen.getByTestId("property-footprint") as HTMLInputElement).value).toBe(
      "Resistor_SMD:R_0603_1608Metric",
    );
  });

  it("renders a footprint dropdown when candidates are supplied", () => {
    render(
      <PropertyPanel
        symbol={SAMPLE}
        projectId="p-1"
        footprintCandidates={[
          "Resistor_SMD:R_0603_1608Metric",
          "Resistor_SMD:R_0805_2012Metric",
        ]}
      />,
    );
    const sel = screen.getByTestId("property-footprint") as HTMLSelectElement;
    expect(sel.tagName).toBe("SELECT");
    expect(sel.value).toBe("Resistor_SMD:R_0603_1608Metric");
  });

  it("posts edited fields to /api/ui/ui_symbol_edit_props/<project_id>", async () => {
    const fetcher = vi.fn<typeof fetch>(async () =>
      ok({ ok: true, changed_fields: ["value"] }),
    );
    const onSaved = vi.fn();
    render(
      <PropertyPanel
        symbol={SAMPLE}
        projectId="p-1"
        fetcher={fetcher as unknown as typeof fetch}
        onSaved={onSaved}
      />,
    );
    fireEvent.change(screen.getByTestId("property-value"), {
      target: { value: "4.7k" },
    });
    fireEvent.click(screen.getByTestId("property-save"));
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    const url = String(fetcher.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/api/ui/ui_symbol_edit_props/p-1");
    const init = fetcher.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse(String(init?.body)) as {
      args: { value: string; symbol_uuid: string };
    };
    expect(body.args.value).toBe("4.7k");
    expect(body.args.symbol_uuid).toBe("u-1");
    await waitFor(() => screen.getByTestId("property-saved"));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("rejects an empty refdes before the request", async () => {
    const fetcher = vi.fn(async () => ok({ ok: true }));
    render(
      <PropertyPanel
        symbol={SAMPLE}
        projectId="p-1"
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.change(screen.getByTestId("property-refdes"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByTestId("property-save"));
    expect(fetcher).not.toHaveBeenCalled();
    expect(screen.getByTestId("property-error").textContent).toContain("Refdes");
  });

  it("rejects a footprint that is not in fp-lib-table candidates", () => {
    const fetcher = vi.fn(async () => ok({ ok: true }));
    render(
      <PropertyPanel
        symbol={{ ...SAMPLE, footprint: "Made:Up_NotReal" }}
        projectId="p-1"
        footprintCandidates={["Resistor_SMD:R_0603_1608Metric"]}
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.click(screen.getByTestId("property-save"));
    expect(screen.getByTestId("property-error").textContent).toContain(
      "fp-lib-table",
    );
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("surfaces gateway errors as a visible message", async () => {
    const fetcher = vi.fn(async () => err({ ok: false, error: "denied" }));
    render(
      <PropertyPanel
        symbol={SAMPLE}
        projectId="p-1"
        fetcher={fetcher as unknown as typeof fetch}
      />,
    );
    fireEvent.click(screen.getByTestId("property-save"));
    await waitFor(() => screen.getByTestId("property-error"));
    expect(screen.getByTestId("property-error").textContent).toContain("denied");
  });

  it("validates datasheet URL syntax", () => {
    render(<PropertyPanel symbol={SAMPLE} projectId="p-1" />);
    fireEvent.change(screen.getByTestId("property-datasheet"), {
      target: { value: "https://" }, // truly malformed URL
    });
    fireEvent.click(screen.getByTestId("property-save"));
    expect(screen.queryByTestId("property-error")?.textContent ?? "").toContain(
      "Datasheet",
    );
  });
});
