import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PcbCanvas } from "./PcbCanvas";

describe("PcbCanvas", () => {
  beforeEach(() => {
    // happy-dom does not implement WebGL or kicanvas's custom elements,
    // so every test in this file uses the injected `loader` seam to
    // stand in for the real bridge.
  });
  afterEach(() => cleanup());

  it("renders the loading state until the loader resolves", async () => {
    let resolveLoad: (value: { status: "ready"; cached: boolean }) => void = () => {};
    const loader = vi.fn().mockImplementation(
      () =>
        new Promise((res) => {
          resolveLoad = res;
        }),
    );
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("loading");
    resolveLoad({ status: "ready", cached: false });
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("ready"),
    );
  });

  it("renders kicanvas-embed with a kicanvas-source child once loaded", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(
      <PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} name="blinky" />,
    );
    const root = await waitFor(() => {
      const el = screen.getByTestId("pcb-canvas");
      if (el.dataset.status !== "ready") throw new Error("not ready");
      return el;
    });
    const embed = root.querySelector("kicanvas-embed");
    expect(embed).not.toBeNull();
    expect(embed?.getAttribute("controls")).toBe("full");
    const source = embed?.querySelector("kicanvas-source");
    expect(source).not.toBeNull();
    expect(source?.getAttribute("src")).toBe("/examples/blinky/blinky.kicad_pcb");
    expect(source?.getAttribute("name")).toBe("blinky");
  });

  it("remounts the embed when src changes", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    const { rerender } = render(
      <PcbCanvas src="/examples/a.kicad_pcb" loader={loader} />,
    );
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("ready"),
    );
    const firstEmbed = screen.getByTestId("pcb-canvas").querySelector("kicanvas-embed");
    rerender(<PcbCanvas src="/examples/b.kicad_pcb" loader={loader} />);
    await waitFor(() => {
      const source = screen
        .getByTestId("pcb-canvas")
        .querySelector("kicanvas-source");
      expect(source?.getAttribute("src")).toBe("/examples/b.kicad_pcb");
    });
    const secondEmbed = screen.getByTestId("pcb-canvas").querySelector("kicanvas-embed");
    // `key={src}` should force a fresh DOM node when the src URL flips.
    expect(secondEmbed).not.toBe(firstEmbed);
  });

  it("surfaces loader errors as the error state", async () => {
    const loader = vi.fn().mockRejectedValue(new Error("script 404"));
    render(<PcbCanvas src="/examples/blinky/blinky.kicad_pcb" loader={loader} />);
    await waitFor(() =>
      expect(screen.getByTestId("pcb-canvas").dataset.status).toBe("error"),
    );
    expect(screen.getByTestId("pcb-canvas").textContent).toContain("script 404");
  });

  it("honours custom controls + controlslist props", async () => {
    const loader = vi.fn().mockResolvedValue({ status: "ready", cached: true });
    render(
      <PcbCanvas
        src="/examples/blinky/blinky.kicad_pcb"
        loader={loader}
        controls="basic"
        controlslist="nodownload nooverlay"
      />,
    );
    const embed = await waitFor(() => {
      const el = screen.getByTestId("pcb-canvas").querySelector("kicanvas-embed");
      if (!el) throw new Error("no embed yet");
      return el;
    });
    expect(embed.getAttribute("controls")).toBe("basic");
    expect(embed.getAttribute("controlslist")).toBe("nodownload nooverlay");
  });
});
