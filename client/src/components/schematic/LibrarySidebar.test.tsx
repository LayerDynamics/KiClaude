import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LibrarySidebar, type LibrarySearchHit } from "./LibrarySidebar";

const SAMPLE_HIT: LibrarySearchHit = {
  lib_id: "Device:R",
  name: "R",
  library: "Device",
  description: "Resistor",
  footprint_filter: "R_*",
  reference: "R",
  value: "R",
  footprint: "",
  datasheet: "",
  mpn: "",
  is_power: false,
  score: 1.0,
};

afterEach(() => cleanup());

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("LibrarySidebar", () => {
  it("calls the search endpoint with the typed query (debounced)", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ hits: [SAMPLE_HIT] }));
    render(<LibrarySidebar projectId="p-1" fetcher={fetcher as unknown as typeof fetch} />);
    const input = screen.getByTestId("library-search-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "STM32" } });
    expect(fetcher).not.toHaveBeenCalled();
    act(() => {
      vi.advanceTimersByTime(200);
    });
    vi.useRealTimers();
    await waitFor(() => expect(fetcher).toHaveBeenCalled());
    const callArg = String(fetcher.mock.calls[0]?.[0] ?? "");
    expect(callArg).toContain("/library/search");
    expect(callArg).toContain("q=STM32");
  });

  it("renders ranked hits with name + library + description", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ hits: [SAMPLE_HIT] }));
    render(<LibrarySidebar projectId="p-1" fetcher={fetcher as unknown as typeof fetch} />);
    await waitFor(() => screen.getByTestId("library-hit-Device:R"));
    const row = screen.getByTestId("library-hit-Device:R");
    expect(row.textContent).toContain("R");
    expect(row.textContent).toContain("Device");
    expect(row.textContent).toContain("Resistor");
  });

  it("renders an error banner on fetch failure", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ detail: "boom" }, 500));
    render(<LibrarySidebar projectId="p-1" fetcher={fetcher as unknown as typeof fetch} />);
    await waitFor(() => screen.getByTestId("library-error"));
    expect(screen.getByTestId("library-error").textContent).toMatch(/500/);
  });

  it("renders the 'no matches' row when the server returns an empty list", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ hits: [] }));
    render(<LibrarySidebar projectId="p-1" fetcher={fetcher as unknown as typeof fetch} />);
    await waitFor(() => screen.getByTestId("library-empty"));
    expect(screen.getByTestId("library-empty").textContent).toContain("no matches");
  });

  it("attaches the lib_id and full hit payload to the dragstart dataTransfer", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => jsonResponse({ hits: [SAMPLE_HIT] }));
    const onDragSymbolStart = vi.fn();
    render(
      <LibrarySidebar
        projectId="p-1"
        fetcher={fetcher as unknown as typeof fetch}
        onDragSymbolStart={onDragSymbolStart}
      />,
    );
    const row = await waitFor(() => screen.getByTestId("library-hit-Device:R"));
    const recorded: Record<string, string> = {};
    const dt = {
      setData: (key: string, val: string) => {
        recorded[key] = val;
      },
      effectAllowed: "" as DataTransfer["effectAllowed"],
    } as unknown as DataTransfer;
    fireEvent.dragStart(row, { dataTransfer: dt });
    expect(recorded["application/x-kiclaude-lib-id"]).toBe("Device:R");
    expect(recorded["application/x-kiclaude-symbol-hit"]).toBeTruthy();
    expect(onDragSymbolStart).toHaveBeenCalledWith(SAMPLE_HIT);
  });
});
