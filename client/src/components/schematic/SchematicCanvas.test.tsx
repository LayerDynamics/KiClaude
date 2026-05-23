import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchematicCanvas } from "./SchematicCanvas";

afterEach(() => cleanup());

describe("SchematicCanvas", () => {
  it("renders the loading state while kicanvas loads", () => {
    const loader = vi.fn().mockImplementation(() => new Promise(() => {}));
    render(<SchematicCanvas src="/examples/blinky/blinky.kicad_sch" loader={loader} />);
    expect(screen.getByTestId("schematic-canvas").dataset.status).toBe("loading");
  });

  it("renders the kicanvas embed pointing at the schematic source", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(<SchematicCanvas src="/examples/blinky/blinky.kicad_sch" loader={loader} />);
    await waitFor(() =>
      expect(screen.getByTestId("schematic-canvas").dataset.status).toBe("ready"),
    );
    const source = screen.getByTestId("schematic-kicanvas-source");
    expect(source.getAttribute("src")).toBe("/examples/blinky/blinky.kicad_sch");
    expect(source.getAttribute("type")).toBe("schematic");
  });

  it("draws each selection rect from props", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(
      <SchematicCanvas
        src="/examples/blinky/blinky.kicad_sch"
        loader={loader}
        selection={[
          { x: 10, y: 20, width: 30, height: 40, label: "R1" },
          { x: 80, y: 90, width: 10, height: 10 },
        ]}
      />,
    );
    await waitFor(() => screen.getByTestId("schematic-selection-overlay"));
    const rects = screen.getAllByTestId("selection-rect");
    expect(rects).toHaveLength(2);
    expect(rects[0]?.getAttribute("x")).toBe("10");
    expect(rects[0]?.getAttribute("width")).toBe("30");
  });

  it("renders a snap-preview marker when `snap` is provided", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(
      <SchematicCanvas
        src="/examples/blinky/blinky.kicad_sch"
        loader={loader}
        snap={{ x: 100, y: 50, label: "R1 → (50.8, 50.8) mm" }}
      />,
    );
    await waitFor(() => screen.getByTestId("schematic-snap-preview"));
    const marker = screen.getByTestId("snap-marker");
    expect(marker.getAttribute("cx")).toBe("100");
    expect(marker.getAttribute("cy")).toBe("50");
  });

  it("rubber-bands a selection on pointer drag and fires onSelectionChange", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    const onSelectionChange = vi.fn();
    render(
      <SchematicCanvas
        src="/examples/blinky/blinky.kicad_sch"
        loader={loader}
        onSelectionChange={onSelectionChange}
      />,
    );
    const wrapper = await waitFor(() => screen.getByTestId("schematic-canvas"));
    // happy-dom doesn't compute bounding rects by default; stub it.
    wrapper.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 }) as DOMRect;

    fireEvent.pointerDown(wrapper, { button: 0, clientX: 10, clientY: 10, pointerId: 1 });
    fireEvent.pointerMove(wrapper, { clientX: 110, clientY: 60, pointerId: 1 });
    const rubber = screen.getByTestId("rubber-band");
    expect(rubber.getAttribute("width")).toBe("100");
    expect(rubber.getAttribute("height")).toBe("50");
    fireEvent.pointerUp(wrapper, { clientX: 110, clientY: 60, pointerId: 1 });

    expect(onSelectionChange).toHaveBeenCalledTimes(1);
    const arg = onSelectionChange.mock.calls[0]?.[0];
    expect(arg).toMatchObject({ x: 10, y: 10, width: 100, height: 50 });
  });

  it("suppresses the rubber-band callback for a near-zero click", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    const onSelectionChange = vi.fn();
    render(
      <SchematicCanvas
        src="/examples/blinky/blinky.kicad_sch"
        loader={loader}
        onSelectionChange={onSelectionChange}
      />,
    );
    const wrapper = await waitFor(() => screen.getByTestId("schematic-canvas"));
    wrapper.getBoundingClientRect = () =>
      ({ left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 }) as DOMRect;

    fireEvent.pointerDown(wrapper, { button: 0, clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.pointerUp(wrapper, { clientX: 51, clientY: 51, pointerId: 1 });
    expect(onSelectionChange).toHaveBeenCalledWith(null);
  });

  it("surfaces loader errors with data-status='error'", async () => {
    const loader = vi.fn().mockRejectedValue(new Error("script 404"));
    render(<SchematicCanvas src="/examples/blinky/blinky.kicad_sch" loader={loader} />);
    await waitFor(() =>
      expect(screen.getByTestId("schematic-canvas").dataset.status).toBe("error"),
    );
    expect(screen.getByTestId("schematic-canvas").textContent).toContain("script 404");
  });
});
