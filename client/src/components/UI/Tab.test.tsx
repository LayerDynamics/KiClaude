import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Tab } from "./Tab";
import { TabGroup } from "./TabGroup";
import { TabSidebar } from "./TabSidebar";

describe("Tab + TabGroup", () => {
  it("renders one trigger per child Tab and routes by `value`", () => {
    render(
      <TabGroup defaultValue="a">
        <Tab value="a" label="Alpha">
          panel-a
        </Tab>
        <Tab value="b" label="Beta">
          panel-b
        </Tab>
      </TabGroup>,
    );
    expect(screen.getByTestId("tab-trigger-a")).toBeTruthy();
    expect(screen.getByTestId("tab-trigger-b")).toBeTruthy();
    expect(screen.getByTestId("tab-content-a").textContent).toBe("panel-a");
    // Inactive content is rendered but hidden via data-state=inactive.
    const inactive = screen.getByTestId("tab-content-b");
    expect(inactive.getAttribute("data-state")).toBe("inactive");
  });

  it("clicking a trigger switches the active panel and fires onValueChange", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <TabGroup defaultValue="a" onValueChange={onChange}>
        <Tab value="a" label="Alpha">
          A
        </Tab>
        <Tab value="b" label="Beta">
          B
        </Tab>
      </TabGroup>,
    );
    await user.click(screen.getByTestId("tab-trigger-b"));
    expect(onChange).toHaveBeenCalledWith("b");
    expect(
      screen.getByTestId("tab-content-b").getAttribute("data-state"),
    ).toBe("active");
  });

  it("disabled tabs are not focusable as active", () => {
    render(
      <TabGroup defaultValue="a">
        <Tab value="a" label="Alpha">
          A
        </Tab>
        <Tab value="b" label="Beta" disabled>
          B
        </Tab>
      </TabGroup>,
    );
    const disabled = screen.getByTestId(
      "tab-trigger-b",
    ) as HTMLButtonElement;
    expect(disabled.disabled).toBe(true);
  });

  it("falls back to the first non-disabled tab when no defaultValue is provided", () => {
    render(
      <TabGroup>
        <Tab value="a" label="Alpha" disabled>
          A
        </Tab>
        <Tab value="b" label="Beta">
          B
        </Tab>
      </TabGroup>,
    );
    expect(
      screen.getByTestId("tab-trigger-b").getAttribute("data-state"),
    ).toBe("active");
  });

  it("keepMounted renders inactive panel content into the DOM", () => {
    render(
      <TabGroup defaultValue="a">
        <Tab value="a" label="Alpha">
          A
        </Tab>
        <Tab value="b" label="Beta" keepMounted>
          B-kept
        </Tab>
      </TabGroup>,
    );
    // forceMount means the content node is in the tree even when inactive.
    expect(screen.getByTestId("tab-content-b").textContent).toBe("B-kept");
  });

  it("vertical orientation flips the layout flexbox to row", () => {
    render(
      <TabGroup defaultValue="a" orientation="vertical">
        <Tab value="a" label="A">
          A
        </Tab>
        <Tab value="b" label="B">
          B
        </Tab>
      </TabGroup>,
    );
    const root = screen.getByTestId("tab-group");
    expect(root.className).toContain("flex-row");
    expect(root.getAttribute("data-orientation")).toBe("vertical");
  });
});

describe("TabSidebar", () => {
  it("renders a vertical sidebar with one trigger per child", () => {
    render(
      <TabSidebar defaultValue="b">
        <Tab value="a" label="Alpha">
          A
        </Tab>
        <Tab value="b" label="Beta">
          B
        </Tab>
      </TabSidebar>,
    );
    const root = screen.getByTestId("tab-sidebar");
    expect(root.getAttribute("data-edge")).toBe("left");
    expect(root.className).toContain("flex-row");
    expect(screen.getByTestId("tab-sidebar-trigger-a")).toBeTruthy();
    expect(
      screen.getByTestId("tab-sidebar-trigger-b").getAttribute("data-state"),
    ).toBe("active");
  });

  it("edge=right reverses the row direction", () => {
    render(
      <TabSidebar defaultValue="a" edge="right">
        <Tab value="a" label="A">
          A
        </Tab>
      </TabSidebar>,
    );
    const root = screen.getByTestId("tab-sidebar");
    expect(root.getAttribute("data-edge")).toBe("right");
    expect(root.className).toContain("flex-row-reverse");
  });
});
