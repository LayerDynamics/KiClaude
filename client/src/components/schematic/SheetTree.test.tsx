import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SheetTree, type SheetNode } from "./SheetTree";

afterEach(() => cleanup());

const ROOT: SheetNode = { uuid: "r", name: "root", file: "root.kicad_sch", parent: null };
const A: SheetNode = { uuid: "a", name: "Power", file: "power.kicad_sch", parent: "r" };
const B: SheetNode = { uuid: "b", name: "MCU", file: "mcu.kicad_sch", parent: "r" };
const C: SheetNode = { uuid: "c", name: "Buck3V3", file: "buck.kicad_sch", parent: "a" };

describe("SheetTree", () => {
  it("renders every sheet with indentation per depth", () => {
    render(
      <SheetTree
        sheets={[ROOT, A, B, C]}
        activeSheetUuid="r"
      />,
    );
    const root = screen.getByTestId("sheet-tree-row-r");
    const power = screen.getByTestId("sheet-tree-row-a");
    const buck = screen.getByTestId("sheet-tree-row-c");
    // Indentation correlates with depth via padding-left.
    expect(getPadLeft(root)).toBe(0);
    expect(getPadLeft(power)).toBe(12);
    expect(getPadLeft(buck)).toBe(24);
  });

  it("highlights the active row", () => {
    render(<SheetTree sheets={[ROOT, A]} activeSheetUuid="a" />);
    expect(screen.getByTestId("sheet-tree-row-a").dataset.active).toBe("true");
    expect(screen.getByTestId("sheet-tree-row-r").dataset.active).toBe("false");
  });

  it("fires onNavigate when a row is clicked", () => {
    const onNavigate = vi.fn();
    render(
      <SheetTree
        sheets={[ROOT, A]}
        activeSheetUuid="r"
        onNavigate={onNavigate}
      />,
    );
    const btn = screen
      .getByTestId("sheet-tree-row-a")
      .querySelector("button") as HTMLButtonElement;
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith("a");
  });

  it("renders the breadcrumb path to the active sheet", () => {
    render(<SheetTree sheets={[ROOT, A, C]} activeSheetUuid="c" />);
    const crumb = screen.getByTestId("sheet-breadcrumb");
    expect(crumb.textContent).toContain("root");
    expect(crumb.textContent).toContain("Power");
    expect(crumb.textContent).toContain("Buck3V3");
    // The active step gets data-testid suffixed with the uuid.
    expect(screen.getByTestId("sheet-breadcrumb-c").textContent).toBe(
      "Buck3V3",
    );
  });

  it("renders the empty state when there are no sheets", () => {
    render(<SheetTree sheets={[]} activeSheetUuid={null} />);
    expect(screen.getByTestId("sheet-tree-empty").textContent).toContain(
      "no sheets",
    );
  });

  it("surfaces an orphan sheet at depth 0 instead of dropping it", () => {
    const orphan: SheetNode = {
      uuid: "x",
      name: "Orphan",
      file: "x.kicad_sch",
      parent: "missing",
    };
    render(<SheetTree sheets={[ROOT, orphan]} activeSheetUuid="r" />);
    const row = screen.getByTestId("sheet-tree-row-x");
    expect(getPadLeft(row)).toBe(0);
    expect(row.textContent).toContain("Orphan");
  });

  it("breaks parent cycles defensively without infinite recursion", () => {
    // Hand-craft a cycle: a → b → a.
    const aLoop: SheetNode = { uuid: "a2", name: "A", file: "a.kicad_sch", parent: "b2" };
    const bLoop: SheetNode = { uuid: "b2", name: "B", file: "b.kicad_sch", parent: "a2" };
    render(
      <SheetTree
        sheets={[aLoop, bLoop]}
        activeSheetUuid="a2"
      />,
    );
    // Both are surfaced once; no crash.
    expect(screen.getByTestId("sheet-tree-row-a2")).toBeTruthy();
    expect(screen.getByTestId("sheet-tree-row-b2")).toBeTruthy();
  });
});

function getPadLeft(el: HTMLElement): number {
  return Number.parseInt(el.style.paddingLeft || "0", 10);
}
