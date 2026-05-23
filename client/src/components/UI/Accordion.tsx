import type { ReactNode, Ref } from "react";
import { Accordion as RadixAccordion } from "radix-ui";

export interface AccordionItem {
  id: string;
  title: ReactNode;
  /** Optional right-aligned content rendered in the trigger row
   *  (e.g. a count badge). */
  meta?: ReactNode;
  content: ReactNode;
  disabled?: boolean;
}

interface AccordionBaseProps {
  items: AccordionItem[];
  className?: string;
  /** Renders a chevron indicator on the trigger; default true. */
  showIndicator?: boolean;
  /** Item id(s) that start expanded when uncontrolled. */
  defaultValue?: string | string[];
  ref?: Ref<HTMLDivElement>;
}

interface AccordionSingleProps extends AccordionBaseProps {
  type: "single";
  collapsible?: boolean;
  value?: string;
  onValueChange?: (value: string) => void;
  defaultValue?: string;
}

interface AccordionMultipleProps extends AccordionBaseProps {
  type: "multiple";
  value?: string[];
  onValueChange?: (value: string[]) => void;
  defaultValue?: string[];
}

export type AccordionProps = AccordionSingleProps | AccordionMultipleProps;

/**
 * Accordion — thin Radix wrapper that takes a flat `items` array
 * and renders each as a labeled, collapsible row. Use `type="single"`
 * for at-most-one-open (and `collapsible` to allow zero-open),
 * `type="multiple"` for independent toggles.
 */
export function Accordion(props: AccordionProps) {
  const {
    items,
    className = "",
    showIndicator = true,
    ref,
  } = props;

  const itemCls =
    "border-b border-[var(--border)] last:border-b-0";
  const triggerCls =
    "group flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm font-medium text-[var(--text-h)] hover:bg-[var(--code-bg)] focus:outline-none focus-visible:bg-[var(--code-bg)] disabled:cursor-not-allowed disabled:opacity-50";
  const contentCls =
    "overflow-hidden px-3 py-2 text-sm text-[var(--text)] data-[state=closed]:hidden";
  const rootCls =
    `flex flex-col rounded-md border border-[var(--border)] bg-[var(--bg)] ${className}`.trim();

  const body = items.map((item) => (
    <RadixAccordion.Item
      key={item.id}
      value={item.id}
      disabled={item.disabled}
      className={itemCls}
      data-testid={`accordion-item-${item.id}`}
    >
      <RadixAccordion.Header asChild>
        <h3 className="m-0">
          <RadixAccordion.Trigger
            data-testid={`accordion-trigger-${item.id}`}
            className={triggerCls}
          >
            <span className="min-w-0 flex-1 truncate">{item.title}</span>
            <span className="flex items-center gap-2">
              {item.meta !== undefined ? (
                <span className="text-xs text-[var(--text)]/70">
                  {item.meta}
                </span>
              ) : null}
              {showIndicator ? (
                <span
                  aria-hidden="true"
                  className="text-[var(--text)]/60 transition-transform group-data-[state=open]:rotate-90"
                >
                  ▸
                </span>
              ) : null}
            </span>
          </RadixAccordion.Trigger>
        </h3>
      </RadixAccordion.Header>
      <RadixAccordion.Content
        data-testid={`accordion-content-${item.id}`}
        className={contentCls}
      >
        {item.content}
      </RadixAccordion.Content>
    </RadixAccordion.Item>
  ));

  if (props.type === "single") {
    const { collapsible, value, onValueChange, defaultValue } = props;
    return (
      <RadixAccordion.Root
        ref={ref}
        type="single"
        collapsible={collapsible}
        value={value}
        onValueChange={onValueChange}
        defaultValue={defaultValue}
        className={rootCls}
      >
        {body}
      </RadixAccordion.Root>
    );
  }
  const { value, onValueChange, defaultValue } = props;
  return (
    <RadixAccordion.Root
      ref={ref}
      type="multiple"
      value={value}
      onValueChange={onValueChange}
      defaultValue={defaultValue}
      className={rootCls}
    >
      {body}
    </RadixAccordion.Root>
  );
}
