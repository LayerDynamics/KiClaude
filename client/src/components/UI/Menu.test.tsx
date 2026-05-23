import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { Menu, type MenuItem } from "./Menu";

const ITEMS: MenuItem[] = [
  { id: "open", label: "Open" },
  { id: "save", label: "Save", shortcut: "⌘S", accessory: <span>2</span> },
  { id: "del", label: "Delete", disabled: true },
  { id: "with-hint", label: "Export", hint: "Gerber, drill, P&P, BOM" },
];

describe("Menu", () => {
  it("renders one button per item with role=menuitem", () => {
    render(<Menu items={ITEMS} />);
    for (const item of ITEMS) {
      const btn = screen.getByTestId(`menu-item-${item.id}`);
      expect(btn.tagName).toBe("BUTTON");
      expect(btn.getAttribute("role")).toBe("menuitem");
    }
  });

  it("fires onSelect with the id when a row is clicked", () => {
    const handle = vi.fn();
    render(<Menu items={ITEMS} onSelect={handle} />);
    fireEvent.click(screen.getByTestId("menu-item-open"));
    expect(handle).toHaveBeenCalledWith("open");
  });

  it("calls per-item onSelect in addition to the group onSelect", () => {
    const onItem = vi.fn();
    const onGroup = vi.fn();
    const items: MenuItem[] = [{ id: "x", label: "X", onSelect: onItem }];
    render(<Menu items={items} onSelect={onGroup} />);
    fireEvent.click(screen.getByTestId("menu-item-x"));
    expect(onItem).toHaveBeenCalledOnce();
    expect(onGroup).toHaveBeenCalledWith("x");
  });

  it("disabled items are non-clickable and fire neither handler", () => {
    const handle = vi.fn();
    render(<Menu items={ITEMS} onSelect={handle} />);
    const btn = screen.getByTestId("menu-item-del") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(handle).not.toHaveBeenCalled();
  });

  it("marks the active row with data-active=true and the accent class", () => {
    render(<Menu items={ITEMS} activeId="save" />);
    const active = screen.getByTestId("menu-item-save");
    expect(active.getAttribute("data-active")).toBe("true");
    expect(active.className).toContain("--accent-bg");
  });

  it("renders shortcut, accessory, and hint text when supplied", () => {
    render(<Menu items={ITEMS} />);
    expect(screen.getByTestId("menu-item-save").textContent).toContain("⌘S");
    expect(screen.getByTestId("menu-item-save").textContent).toContain("2");
    expect(screen.getByTestId("menu-item-with-hint").textContent).toContain(
      "Gerber, drill, P&P, BOM",
    );
  });
});
