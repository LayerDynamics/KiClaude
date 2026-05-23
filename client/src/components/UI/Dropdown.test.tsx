import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Dropdown, type DropdownItem } from "./Dropdown";

const ITEMS: DropdownItem[] = [
  { kind: "label", id: "lbl", label: "File" },
  { kind: "item", id: "open", label: "Open", shortcut: "⌘O" },
  { kind: "item", id: "save", label: "Save", onSelect: vi.fn() },
  { kind: "separator", id: "sep" },
  { kind: "item", id: "del", label: "Delete", danger: true },
  { kind: "item", id: "noop", label: "Noop", disabled: true },
];

describe("Dropdown", () => {
  it("the menu content is closed until the trigger is clicked", async () => {
    const user = userEvent.setup();
    render(
      <Dropdown
        trigger={<button data-testid="trg">Menu</button>}
        items={ITEMS}
      />,
    );
    expect(screen.queryByTestId("dropdown-content")).toBeNull();
    await user.click(screen.getByTestId("trg"));
    expect(screen.getByTestId("dropdown-content")).toBeTruthy();
  });

  it("renders label / items / separator and fires onSelect when clicked", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const items: DropdownItem[] = [
      { kind: "label", id: "L", label: "Group" },
      { kind: "item", id: "go", label: "Go", onSelect },
      { kind: "separator", id: "s" },
      { kind: "item", id: "noop", label: "Noop", disabled: true },
    ];
    render(
      <Dropdown
        trigger={<button data-testid="trg">m</button>}
        items={items}
      />,
    );
    await user.click(screen.getByTestId("trg"));
    expect(screen.getByTestId("dropdown-label-L").textContent).toBe("Group");
    expect(screen.getByTestId("dropdown-separator-s")).toBeTruthy();
    expect(
      (screen.getByTestId("dropdown-item-noop") as HTMLButtonElement)
        .getAttribute("data-disabled"),
    ).not.toBeNull();

    await user.click(screen.getByTestId("dropdown-item-go"));
    expect(onSelect).toHaveBeenCalledOnce();
  });

  it("controlled open state stays in sync with the open prop", () => {
    const { rerender } = render(
      <Dropdown
        trigger={<button data-testid="trg">m</button>}
        items={ITEMS}
        open={false}
      />,
    );
    expect(screen.queryByTestId("dropdown-content")).toBeNull();
    rerender(
      <Dropdown
        trigger={<button data-testid="trg">m</button>}
        items={ITEMS}
        open={true}
      />,
    );
    expect(screen.getByTestId("dropdown-content")).toBeTruthy();
  });

  it("shortcut text is rendered in the row", () => {
    render(
      <Dropdown
        trigger={<button data-testid="trg">m</button>}
        items={ITEMS}
        open={true}
      />,
    );
    expect(screen.getByTestId("dropdown-item-open").textContent).toContain(
      "⌘O",
    );
  });
});
