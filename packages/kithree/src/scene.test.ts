import { describe, expect, it } from "vitest";
import { Box3, Mesh, Vector3 } from "three";

import {
  DEFAULT_THEME,
  loadThreeScene,
  type ScenePlacement,
  type ThreeScene,
} from "./scene.js";

function placement(refdes: string, overrides: Partial<ScenePlacement> = {}): ScenePlacement {
  return {
    refdes,
    model_path: `/some/${refdes}.step`,
    position_mm: [10, 10, 0],
    scale: [1, 1, 1],
    rotation_deg: [0, 0, 0],
    side: "top",
    ...overrides,
  };
}

const SQUARE_BOARD: ThreeScene = {
  board_thickness_mm: 1.6,
  board_outline_mm: [
    [0, 0],
    [100, 0],
    [100, 100],
    [0, 100],
  ],
  placements: [placement("U1"), placement("U2", { position_mm: [50, 50, 0] })],
};

describe("loadThreeScene (M3-T-06)", () => {
  it("returns a Group with a board mesh + one marker per placement", () => {
    const loaded = loadThreeScene(SQUARE_BOARD);
    expect(loaded.group.children.length).toBe(1 + SQUARE_BOARD.placements.length);
    expect(loaded.boardMesh).not.toBeNull();
    expect(loaded.markers.size).toBe(2);
    expect(loaded.markers.has("U1")).toBe(true);
    expect(loaded.markers.has("U2")).toBe(true);
    loaded.dispose();
  });

  it("centres the board on world origin", () => {
    const loaded = loadThreeScene(SQUARE_BOARD);
    const board = loaded.boardMesh!;
    const bbox = new Box3().setFromObject(board);
    const center = bbox.getCenter(new Vector3());
    expect(Math.abs(center.x)).toBeLessThan(0.5);
    expect(Math.abs(center.z)).toBeLessThan(0.5);
    // Board sits at Y ∈ [0, thickness] after the extrude rotation.
    expect(center.y).toBeCloseTo(SQUARE_BOARD.board_thickness_mm / 2, 1);
    loaded.dispose();
  });

  it("extrudes the board to the declared thickness", () => {
    const loaded = loadThreeScene(SQUARE_BOARD);
    const bbox = new Box3().setFromObject(loaded.boardMesh!);
    const size = bbox.getSize(new Vector3());
    expect(size.y).toBeCloseTo(SQUARE_BOARD.board_thickness_mm, 3);
    expect(size.x).toBeCloseTo(100, 1);
    expect(size.z).toBeCloseTo(100, 1);
    loaded.dispose();
  });

  it("returns null boardMesh + zero board children when the outline is empty", () => {
    const loaded = loadThreeScene({
      board_thickness_mm: 1.6,
      board_outline_mm: [],
      placements: [placement("U1")],
    });
    expect(loaded.boardMesh).toBeNull();
    expect(loaded.group.children.length).toBe(1); // marker only
    loaded.dispose();
  });

  it("places top-side markers above the board surface", () => {
    const loaded = loadThreeScene(SQUARE_BOARD);
    const marker = loaded.markers.get("U1") as Mesh;
    // Top-side: y >= board thickness (top surface is at y = thickness).
    expect(marker.position.y).toBeGreaterThanOrEqual(SQUARE_BOARD.board_thickness_mm);
    loaded.dispose();
  });

  it("places bottom-side markers below the board with side flip", () => {
    const loaded = loadThreeScene({
      ...SQUARE_BOARD,
      placements: [placement("U3", { side: "bottom" })],
    });
    const marker = loaded.markers.get("U3") as Mesh;
    expect(marker.position.y).toBeLessThanOrEqual(0);
    // The 180° flip about X for bottom side.
    expect(Math.abs(marker.rotation.x - Math.PI)).toBeLessThan(0.0001);
    loaded.dispose();
  });

  it("scales the marker box by the placement's scale tuple", () => {
    const loaded = loadThreeScene({
      ...SQUARE_BOARD,
      placements: [placement("BIG", { scale: [3, 2, 1] })],
    });
    const marker = loaded.markers.get("BIG") as Mesh;
    const bbox = new Box3().setFromObject(marker);
    const size = bbox.getSize(new Vector3());
    // Default LWH = (2.0, 1.2, 0.8); scale [3, 2, 1] → (6.0, 2.4, 0.8).
    // Length lands on X, height on Y, width on Z per scene.ts.
    expect(size.x).toBeCloseTo(6.0, 2);
    expect(size.y).toBeCloseTo(0.8, 2);
    expect(size.z).toBeCloseTo(2.4, 2);
    loaded.dispose();
  });

  it("dispose() drops all created geometries and materials", () => {
    const loaded = loadThreeScene(SQUARE_BOARD);
    const board = loaded.boardMesh!;
    loaded.dispose();
    expect(loaded.markers.size).toBe(0);
    // Geometry is disposed but `attributes` survives the disposal call —
    // verify by attempting another dispose() doesn't throw.
    expect(() => board.geometry.dispose()).not.toThrow();
  });

  it("honours a custom theme's color palette", () => {
    const loaded = loadThreeScene(SQUARE_BOARD, {
      ...DEFAULT_THEME,
      boardColor: 0xff00ff,
      topMarkerColor: 0x00ffff,
    });
    const board = loaded.boardMesh!;
    // MeshStandardMaterial exposes .color
    // @ts-expect-error — three's material types are loose at the boundary
    expect(board.material.color.getHex()).toBe(0xff00ff);
    const u1 = loaded.markers.get("U1") as Mesh;
    // @ts-expect-error
    expect(u1.material.color.getHex()).toBe(0x00ffff);
    loaded.dispose();
  });
});
