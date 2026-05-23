import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { Accordion, type AccordionItem } from "./Accordion";

const ITEMS: AccordionItem[] = [
  { id: "one", title: "One", content: <p>body-one</p> },
  { id: "two", title: "Two", meta: <span>2</span>, content: <p>body-two</p> },
  { id: "three", title: "Three", content: <p>body-three</p>, disabled: true },
];

describe("Accordion", () => {
  it("renders one trigger per item and respects defaultValue", () => {
    render(<Accordion type="single" items={ITEMS} defaultValue="two" />);
    for (const item of ITEMS) {
      expect(screen.getByTestId(`accordion-trigger-${item.id}`)).toBeTruthy();
    }
    const trigger = screen.getByTestId("accordion-trigger-two");
    expect(trigger.getAttribute("data-state")).toBe("open");
  });

  it("type=single toggles other items closed when collapsible is omitted", () => {
    render(<Accordion type="single" items={ITEMS} defaultValue="one" />);
    fireEvent.click(screen.getByTestId("accordion-trigger-two"));
    expect(
      screen.getByTestId("accordion-trigger-one").getAttribute("data-state"),
    ).toBe("closed");
    expect(
      screen.getByTestId("accordion-trigger-two").getAttribute("data-state"),
    ).toBe("open");
  });

  it("type=single + collapsible allows closing the open item", () => {
    render(
      <Accordion
        type="single"
        collapsible
        items={ITEMS}
        defaultValue="one"
      />,
    );
    fireEvent.click(screen.getByTestId("accordion-trigger-one"));
    expect(
      screen.getByTestId("accordion-trigger-one").getAttribute("data-state"),
    ).toBe("closed");
  });

  it("type=multiple keeps both items open independently", () => {
    render(<Accordion type="multiple" items={ITEMS} defaultValue={["one"]} />);
    fireEvent.click(screen.getByTestId("accordion-trigger-two"));
    expect(
      screen.getByTestId("accordion-trigger-one").getAttribute("data-state"),
    ).toBe("open");
    expect(
      screen.getByTestId("accordion-trigger-two").getAttribute("data-state"),
    ).toBe("open");
  });

  it("disabled items cannot be opened and fire no value change", () => {
    const onChange = vi.fn();
    render(
      <Accordion
        type="single"
        items={ITEMS}
        onValueChange={onChange}
      />,
    );
    const disabled = screen.getByTestId(
      "accordion-trigger-three",
    ) as HTMLButtonElement;
    expect(disabled.disabled).toBe(true);
    fireEvent.click(disabled);
    expect(onChange).not.toHaveBeenCalled();
  });

  it("renders meta content in the trigger row", () => {
    render(<Accordion type="single" items={ITEMS} />);
    expect(screen.getByTestId("accordion-trigger-two").textContent).toContain(
      "2",
    );
  });
});
