import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { DebugPane } from "./DebugPane";

describe("DebugPane", () => {
  it("renders the wasm-bound project JSON containing 'blinky'", async () => {
    const loader = vi.fn().mockResolvedValue({
      ki: {
        openProjectFromStrings: vi.fn().mockReturnValue({
          name: "blinky",
          kcir_version: "0.1.0",
          pcb: { layers: [], footprints: [], tracks: [] },
        }),
      },
    });
    render(<DebugPane loader={loader} />);
    const pane = await waitFor(() => screen.getByTestId("debug-pane-result"));
    expect(pane.textContent).toContain("blinky");
    expect(pane.textContent).toContain("kcir_version");
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("renders an error message when the loader rejects", async () => {
    const loader = vi.fn().mockRejectedValue(new Error("wasm init failed"));
    render(<DebugPane loader={loader} />);
    const err = await waitFor(() => screen.getByTestId("debug-pane-error"));
    expect(err.textContent).toContain("wasm init failed");
  });
});
