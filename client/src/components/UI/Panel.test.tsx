import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Panel } from "./Panel";

describe("Panel", () => {
  it("renders a string title via Text variant=h4 in the header", () => {
    render(
      <Panel title="Properties">
        <span>body</span>
      </Panel>,
    );
    const header = screen.getByTestId("panel-header");
    expect(header.textContent).toContain("Properties");
    // String titles route through <Text variant="h4">.
    expect(header.querySelector("[data-variant='h4']")).not.toBeNull();
    expect(screen.getByTestId("panel-body").textContent).toBe("body");
  });

  it("renders custom title node verbatim and an actions slot", () => {
    render(
      <Panel
        title={<span data-testid="custom-title">custom</span>}
        actions={<button>×</button>}
      >
        body
      </Panel>,
    );
    expect(screen.getByTestId("custom-title").textContent).toBe("custom");
    expect(screen.getByTestId("panel-actions").querySelector("button")).not.toBeNull();
  });

  it("renders a subtitle below the title", () => {
    render(
      <Panel title="t" subtitle="explainer">
        body
      </Panel>,
    );
    expect(screen.getByTestId("panel-header").textContent).toContain("explainer");
  });

  it("omits the header when neither title nor actions are provided", () => {
    render(<Panel data-testid="p">body</Panel>);
    expect(screen.queryByTestId("panel-header")).toBeNull();
  });

  it("compact density tightens header padding", () => {
    render(
      <Panel title="t" density="compact">
        body
      </Panel>,
    );
    const header = screen.getByTestId("panel-header");
    expect(header.className).toContain("px-3");
    expect(header.className).toContain("py-1.5");
  });

  it("flush body drops body padding; maxBodyHeight sets max-height", () => {
    render(
      <Panel title="t" flush maxBodyHeight={200}>
        body
      </Panel>,
    );
    const body = screen.getByTestId("panel-body");
    expect(body.className).not.toContain(" p-4");
    expect(body.className).toContain("overflow-auto");
    expect(body.style.maxHeight).toBe("200px");
  });
});
