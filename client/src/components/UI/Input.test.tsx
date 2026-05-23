import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { Input } from "./Input";

describe("Input", () => {
  it("renders an input with a label wired by id", () => {
    render(<Input label="Refdes" id="refdes" placeholder="U1" />);
    const field = screen.getByPlaceholderText("U1") as HTMLInputElement;
    expect(field.id).toBe("refdes");
    const label = screen.getByText("Refdes") as HTMLLabelElement;
    expect(label.getAttribute("for")).toBe("refdes");
  });

  it("auto-generates an id when none is provided so the label still works", () => {
    render(<Input label="Value" placeholder="10k" />);
    const field = screen.getByPlaceholderText("10k") as HTMLInputElement;
    expect(field.id.length).toBeGreaterThan(0);
    const label = screen.getByText("Value") as HTMLLabelElement;
    expect(label.getAttribute("for")).toBe(field.id);
  });

  it("renders hint text when supplied", () => {
    render(<Input label="x" hint="explain me" placeholder="p" />);
    expect(screen.getByTestId("input-hint").textContent).toBe("explain me");
  });

  it("error replaces hint, sets aria-invalid, and toggles tone=error", () => {
    const { container } = render(
      <Input
        label="x"
        hint="ignored"
        error="bad"
        placeholder="p"
      />,
    );
    expect(screen.getByTestId("input-error").textContent).toBe("bad");
    expect(screen.queryByTestId("input-hint")).toBeNull();
    const field = screen.getByPlaceholderText("p") as HTMLInputElement;
    expect(field.getAttribute("aria-invalid")).toBe("true");
    const wrapper = container.firstElementChild as HTMLElement;
    expect(wrapper.getAttribute("data-tone")).toBe("error");
  });

  it("fires onChange and respects controlled value", () => {
    const handle = vi.fn();
    render(<Input placeholder="p" value="hello" onChange={handle} />);
    const field = screen.getByPlaceholderText("p") as HTMLInputElement;
    expect(field.value).toBe("hello");
    fireEvent.change(field, { target: { value: "world" } });
    expect(handle).toHaveBeenCalledTimes(1);
  });

  it("renders leading and trailing addons", () => {
    render(
      <Input
        placeholder="p"
        leadingAddon={<span>$</span>}
        trailingAddon={<span>USD</span>}
      />,
    );
    expect(screen.getByTestId("input-leading").textContent).toBe("$");
    expect(screen.getByTestId("input-trailing").textContent).toBe("USD");
  });
});
