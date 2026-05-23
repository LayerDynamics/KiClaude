import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Data, type DataItem } from "./Data";

const ITEMS: DataItem[] = [
  { key: "refdes", label: "Refdes", value: "U1" },
  { key: "value", label: "Value", value: "10k", hint: "1% tol" },
  { key: "mpn", label: "MPN", value: "MPN-123", action: <button>copy</button> },
];

describe("Data", () => {
  it("renders one row per item with label / value / hint / action", () => {
    render(<Data items={ITEMS} />);
    for (const item of ITEMS) {
      expect(screen.getByTestId(`data-row-${item.key}`)).toBeTruthy();
      expect(screen.getByTestId(`data-label-${item.key}`).textContent).toBe(
        String(item.label),
      );
    }
    expect(screen.getByTestId("data-value-refdes").textContent).toBe("U1");
    expect(screen.getByTestId("data-value-value").textContent).toContain(
      "1% tol",
    );
    expect(screen.getByTestId("data-action-mpn")).toBeTruthy();
  });

  it("uses semantic dl/dt/dd elements", () => {
    const { container } = render(<Data items={ITEMS} />);
    const dl = container.querySelector("dl");
    expect(dl).not.toBeNull();
    expect(dl!.querySelectorAll("dt").length).toBe(ITEMS.length);
    expect(dl!.querySelectorAll("dd").length).toBe(ITEMS.length);
  });

  it("layout=rows omits the action column and stacks label above value", () => {
    render(<Data items={ITEMS} layout="rows" />);
    expect(screen.queryByTestId("data-action-mpn")).toBeNull();
    const row = screen.getByTestId("data-row-refdes");
    expect(row.className).toContain("flex-col");
  });

  it("renders the empty state when items=[]", () => {
    render(<Data items={[]} empty="nothing here" />);
    const empty = screen.getByTestId("data-empty");
    expect(empty.textContent).toBe("nothing here");
  });
});
