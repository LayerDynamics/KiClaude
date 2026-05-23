import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Card } from "./Card";

describe("Card", () => {
  it("renders body and applies default tone classes", () => {
    render(
      <Card data-testid="card">
        <span>content</span>
      </Card>,
    );
    const card = screen.getByTestId("card");
    expect(card.getAttribute("data-tone")).toBe("default");
    expect(card.className).toContain("border");
    expect(screen.getByTestId("card-body").textContent).toBe("content");
    expect(screen.queryByTestId("card-header")).toBeNull();
    expect(screen.queryByTestId("card-footer")).toBeNull();
  });

  it("renders header and footer when supplied", () => {
    render(
      <Card header={<span>head</span>} footer={<span>foot</span>}>
        body
      </Card>,
    );
    expect(screen.getByTestId("card-header").textContent).toBe("head");
    expect(screen.getByTestId("card-footer").textContent).toBe("foot");
  });

  it("drops body padding when flush is set", () => {
    render(
      <Card flush data-testid="card">
        x
      </Card>,
    );
    const body = screen.getByTestId("card-body");
    expect(body.className).not.toContain("p-4");
  });

  it("applies the tone variant class", () => {
    render(
      <Card tone="accent" data-testid="card">
        x
      </Card>,
    );
    const card = screen.getByTestId("card");
    expect(card.getAttribute("data-tone")).toBe("accent");
    expect(card.className).toContain("--accent-border");
  });
});
