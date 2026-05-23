import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Container } from "./Container";

describe("Container", () => {
  it("defaults to a div with size=lg and pad=md", () => {
    render(<Container data-testid="c">body</Container>);
    const el = screen.getByTestId("c");
    expect(el.tagName).toBe("DIV");
    expect(el.getAttribute("data-container-size")).toBe("lg");
    expect(el.getAttribute("data-container-pad")).toBe("md");
    expect(el.className).toContain("max-w-screen-lg");
    expect(el.className).toContain("p-4");
  });

  it("respects size and pad props", () => {
    render(
      <Container size="sm" pad="lg" data-testid="c">
        body
      </Container>,
    );
    const el = screen.getByTestId("c");
    expect(el.className).toContain("max-w-screen-sm");
    expect(el.className).toContain("p-6");
  });

  it("renders alternative tag via `as`", () => {
    render(
      <Container as="section" data-testid="c">
        x
      </Container>,
    );
    expect(screen.getByTestId("c").tagName).toBe("SECTION");
  });

  it("size=full uses max-w-full and pad=none drops padding", () => {
    render(
      <Container size="full" pad="none" data-testid="c">
        x
      </Container>,
    );
    const el = screen.getByTestId("c");
    expect(el.className).toContain("max-w-full");
    expect(el.className).toContain("p-0");
  });
});
