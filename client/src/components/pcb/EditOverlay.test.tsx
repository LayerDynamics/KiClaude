import { cleanup, render, screen, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { useKcirStore } from "../../stores/kcirStore";
import { usePcbViewStore } from "../../stores/pcbViewStore";
import { useSelectionStore } from "../../stores/selectionStore";

import { EditOverlay } from "./EditOverlay";

const fp = {
  uuid: "fp-1",
  refdes: "U1",
  lib_id: "Package_DIP:DIP-8_W7.62mm",
  value: "ATMEGA328",
  position_mm: [10, 20] as [number, number],
  rotation_deg: 0,
  locked: false,
};

const track = {
  uuid: "tr-1",
  net: "GND",
  width_mm: 0.5,
  points_mm: [
    [0, 0] as [number, number],
    [5, 0] as [number, number],
    [5, 5] as [number, number],
  ],
};

describe("EditOverlay", () => {
  beforeEach(() => {
    act(() => {
      useSelectionStore.getState().clear();
      useKcirStore.setState({ footprints: [], tracks: [], dirty: false });
      usePcbViewStore.setState({
        layers: [],
        layerView: {},
        activeLayerId: null,
      });
    });
  });
  afterEach(() => cleanup());

  it("returns null when there is nothing selected, hovered, or rubber-banded", () => {
    const { container } = render(<EditOverlay width={400} height={300} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a halo around a selected footprint", () => {
    act(() => {
      useKcirStore.getState().setFootprints([fp]);
      useSelectionStore.getState().select([{ kind: "footprint", uuid: fp.uuid }]);
    });
    render(<EditOverlay width={400} height={300} />);
    const halo = screen.getByTestId("selection-halo");
    expect(halo).toBeTruthy();
    // 1 mm = 4 px at the default transform, centered on (200, 150)
    // → footprint at (10, 20) mm should map to (240, 230) px.
    const circle = halo.querySelector("circle");
    expect(circle?.getAttribute("cx")).toBe("240");
    expect(circle?.getAttribute("cy")).toBe("230");
  });

  it("renders a polyline for a selected track", () => {
    act(() => {
      useKcirStore.getState().setTracks([track]);
      useSelectionStore.getState().select([{ kind: "track", uuid: track.uuid }]);
    });
    render(<EditOverlay width={400} height={300} />);
    const polyline = screen.getByTestId("selection-track");
    expect(polyline.getAttribute("points")).toBe(
      "200,150 220,150 220,170",
    );
  });

  it("dims the selection halo when the active layer is hidden", () => {
    act(() => {
      useKcirStore.getState().setFootprints([fp]);
      useSelectionStore.getState().select([{ kind: "footprint", uuid: fp.uuid }]);
      usePcbViewStore.setState({
        layers: [{ id: 0, name: "F.Cu", kind: "copper" }],
        layerView: { 0: { visible: false, opacity: 1 } },
        activeLayerId: 0,
      });
    });
    render(<EditOverlay width={400} height={300} />);
    const halo = screen.getByTestId("selection-halo");
    expect(halo.querySelector("circle")?.getAttribute("opacity")).toBe("0.35");
  });

  it("draws the rubber-band rectangle", () => {
    render(
      <EditOverlay
        width={400}
        height={300}
        rubber={{ x: 10, y: 20, width: 100, height: 50 }}
      />,
    );
    const r = screen.getByTestId("pcb-rubber-band");
    expect(r.getAttribute("x")).toBe("10");
    expect(r.getAttribute("width")).toBe("100");
  });

  it("renders a hover hint for the hovered footprint", () => {
    act(() => {
      useKcirStore.getState().setFootprints([fp]);
      useSelectionStore.getState().setHovered({ kind: "footprint", uuid: fp.uuid });
    });
    render(<EditOverlay width={400} height={300} />);
    expect(screen.getByTestId("hover-halo")).toBeTruthy();
  });
});
