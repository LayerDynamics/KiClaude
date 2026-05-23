import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Text } from "./Text";

describe("Text", () => {
  it("renders the default body variant as <p>", () => {
    render(<Text data-testid="t">hello</Text>);
    const el = screen.getByTestId("t");
    expect(el.tagName).toBe("P");
    expect(el.getAttribute("data-variant")).toBe("body");
    expect(el.textContent).toBe("hello");
  });

  it("renders headings with the matching tag", () => {
    render(
      <>
        <Text variant="h1" data-testid="h1">
          one
        </Text>
        <Text variant="h2" data-testid="h2">
          two
        </Text>
        <Text variant="h3" data-testid="h3">
          three
        </Text>
        <Text variant="h4" data-testid="h4">
          four
        </Text>
      </>,
    );
    expect(screen.getByTestId("h1").tagName).toBe("H1");
    expect(screen.getByTestId("h2").tagName).toBe("H2");
    expect(screen.getByTestId("h3").tagName).toBe("H3");
    expect(screen.getByTestId("h4").tagName).toBe("H4");
  });

  it("renders mono / caption / label / small with the right tag", () => {
    render(
      <>
        <Text variant="mono" data-testid="mono">
          code
        </Text>
        <Text variant="caption" data-testid="cap">
          cap
        </Text>
        <Text variant="label" data-testid="lab">
          lab
        </Text>
        <Text variant="small" data-testid="sm">
          sm
        </Text>
      </>,
    );
    expect(screen.getByTestId("mono").tagName).toBe("CODE");
    expect(screen.getByTestId("cap").tagName).toBe("SPAN");
    expect(screen.getByTestId("lab").tagName).toBe("LABEL");
    expect(screen.getByTestId("sm").tagName).toBe("P");
  });

  it("respects the `as` override", () => {
    render(
      <Text variant="body" as="section" data-testid="s">
        body-as-section
      </Text>,
    );
    expect(screen.getByTestId("s").tagName).toBe("SECTION");
  });

  it("merges custom className with variant classes", () => {
    render(
      <Text variant="h2" className="custom-tag" data-testid="t">
        x
      </Text>,
    );
    const el = screen.getByTestId("t");
    expect(el.className).toContain("custom-tag");
    expect(el.className).toContain("text-2xl");
  });
});
