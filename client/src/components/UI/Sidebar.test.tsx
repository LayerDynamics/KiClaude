import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Sidebar } from "./Sidebar";

describe("Sidebar", () => {
  it("defaults to edge=right, open=true, width=20rem", () => {
    render(
      <Sidebar title="Chat">
        <p>body</p>
      </Sidebar>,
    );
    const aside = screen.getByTestId("sidebar");
    expect(aside.tagName).toBe("ASIDE");
    expect(aside.getAttribute("data-edge")).toBe("right");
    expect(aside.getAttribute("data-open")).toBe("true");
    expect(aside.style.width).toBe("20rem");
    expect(aside.className).toContain("border-l");
    expect(screen.getByTestId("sidebar-body").textContent).toBe("body");
  });

  it("edge=left renders border-r", () => {
    render(
      <Sidebar edge="left" title="Library">
        body
      </Sidebar>,
    );
    const aside = screen.getByTestId("sidebar");
    expect(aside.getAttribute("data-edge")).toBe("left");
    expect(aside.className).toContain("border-r");
  });

  it("collapsing hides the body and footer", () => {
    render(
      <Sidebar title="t" footer={<span>foot</span>} open={false}>
        body
      </Sidebar>,
    );
    const aside = screen.getByTestId("sidebar");
    expect(aside.getAttribute("data-open")).toBe("false");
    expect(aside.style.width).toBe("2.5rem");
    expect(screen.queryByTestId("sidebar-body")).toBeNull();
    expect(screen.queryByTestId("sidebar-footer")).toBeNull();
  });

  it("renders header actions and footer when open", () => {
    render(
      <Sidebar
        title="t"
        actions={<button data-testid="toggle">+</button>}
        footer={<button data-testid="send">send</button>}
      >
        body
      </Sidebar>,
    );
    expect(screen.getByTestId("toggle")).toBeTruthy();
    expect(screen.getByTestId("send")).toBeTruthy();
    expect(screen.getByTestId("sidebar-actions").textContent).toBe("+");
    expect(screen.getByTestId("sidebar-footer").textContent).toBe("send");
  });
});
