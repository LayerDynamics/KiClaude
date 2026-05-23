import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { InputParagraph } from "./InputParagraph";

describe("InputParagraph", () => {
  it("renders a textarea with the supplied label and id", () => {
    render(
      <InputParagraph
        label="Prompt"
        id="prompt"
        placeholder="say something"
        minRows={2}
      />,
    );
    const ta = screen.getByPlaceholderText("say something") as HTMLTextAreaElement;
    expect(ta.tagName).toBe("TEXTAREA");
    expect(ta.id).toBe("prompt");
    expect(Number(ta.getAttribute("rows"))).toBe(2);
    const label = screen.getByText("Prompt") as HTMLLabelElement;
    expect(label.getAttribute("for")).toBe("prompt");
  });

  it("renders hint and error mutually exclusively", () => {
    const { rerender } = render(
      <InputParagraph hint="help" placeholder="p" />,
    );
    expect(screen.getByTestId("input-paragraph-hint").textContent).toBe("help");
    rerender(<InputParagraph hint="help" error="bad" placeholder="p" />);
    expect(screen.getByTestId("input-paragraph-error").textContent).toBe("bad");
    expect(screen.queryByTestId("input-paragraph-hint")).toBeNull();
  });

  it("fires onInput and onChange and forwards the value", () => {
    const handleInput = vi.fn();
    const handleChange = vi.fn();
    render(
      <InputParagraph
        placeholder="p"
        value=""
        onInput={handleInput}
        onChange={handleChange}
      />,
    );
    const ta = screen.getByPlaceholderText("p") as HTMLTextAreaElement;
    fireEvent.input(ta, { target: { value: "x" } });
    fireEvent.change(ta, { target: { value: "x" } });
    expect(handleInput).toHaveBeenCalled();
    expect(handleChange).toHaveBeenCalled();
  });

  it("disabled prop blocks input", () => {
    render(<InputParagraph placeholder="p" disabled />);
    const ta = screen.getByPlaceholderText("p") as HTMLTextAreaElement;
    expect(ta.disabled).toBe(true);
  });
});
