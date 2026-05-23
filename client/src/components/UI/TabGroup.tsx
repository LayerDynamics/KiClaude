import {
  Children,
  isValidElement,
  type ReactElement,
  type ReactNode,
  type Ref,
} from "react";
import { Tabs as RadixTabs } from "radix-ui";

import { Tab, type TabProps } from "./Tab";

export type TabGroupOrientation = "horizontal" | "vertical";

export interface TabGroupProps {
  /** Direct `<Tab>` children whose props become tab triggers + bodies. */
  children: ReactNode;
  /** Controlled active tab value. */
  value?: string;
  /** Default active tab value when uncontrolled. Falls back to the
   *  first non-disabled child. */
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** Layout direction. `horizontal` (default) renders a top tab bar;
   *  `vertical` renders a left tab strip. For a full vertical sidebar
   *  layout, prefer `TabSidebar`. */
  orientation?: TabGroupOrientation;
  className?: string;
  /** Override the trigger row's class list (e.g. for borderless
   *  variants). */
  listClassName?: string;
  ref?: Ref<HTMLDivElement>;
}

/** Extract `<Tab>` children safely. Non-`Tab` children are ignored
 *  with a console warning so a stray `{null}` or a wrapped component
 *  doesn't silently break the tab strip. */
function extractTabs(children: ReactNode): ReactElement<TabProps>[] {
  const tabs: ReactElement<TabProps>[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    if (child.type !== Tab) {
      if (typeof console !== "undefined") {
        console.warn(
          "TabGroup: ignoring non-Tab child. Wrap content in <Tab value=...> to add it as a tab.",
        );
      }
      return;
    }
    tabs.push(child as ReactElement<TabProps>);
  });
  return tabs;
}

/**
 * TabGroup — Radix Tabs root that takes declarative `<Tab>` children.
 * Builds the trigger row from each child's `value` / `label` /
 * `accessory` / `disabled` props and renders the children as
 * `<Tabs.Content>` panels.
 */
export function TabGroup(props: TabGroupProps) {
  const {
    children,
    value,
    defaultValue,
    onValueChange,
    orientation = "horizontal",
    className = "",
    listClassName = "",
    ref,
  } = props;

  const tabs = extractTabs(children);
  const fallback = tabs.find((t) => !t.props.disabled)?.props.value;
  const effectiveDefault = defaultValue ?? fallback;

  const isHorizontal = orientation === "horizontal";
  const rootCls = `flex ${isHorizontal ? "flex-col" : "flex-row"} ${className}`.trim();
  const defaultListCls = isHorizontal
    ? "flex shrink-0 items-center gap-1 border-b border-[var(--border)] px-2"
    : "flex shrink-0 flex-col gap-1 border-r border-[var(--border)] px-1 py-2";
  const listCls = `${defaultListCls} ${listClassName}`.trim();
  const triggerCls = [
    "inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium text-[var(--text)]",
    "transition-colors hover:text-[var(--text-h)]",
    "data-[state=active]:text-[var(--accent)]",
    "data-[state=active]:bg-[var(--accent-bg)]",
    "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
    "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/40",
  ].join(" ");

  return (
    <RadixTabs.Root
      ref={ref}
      value={value}
      defaultValue={effectiveDefault}
      onValueChange={onValueChange}
      orientation={orientation}
      className={rootCls}
      data-testid="tab-group"
    >
      <RadixTabs.List
        aria-orientation={orientation}
        className={listCls}
        data-testid="tab-list"
      >
        {tabs.map((tab) => (
          <RadixTabs.Trigger
            key={tab.props.value}
            value={tab.props.value}
            disabled={tab.props.disabled}
            data-testid={`tab-trigger-${tab.props.value}`}
            className={triggerCls}
          >
            <span className="min-w-0 truncate">{tab.props.label}</span>
            {tab.props.accessory !== undefined ? (
              <span className="shrink-0 text-xs text-[var(--text)]/70">
                {tab.props.accessory}
              </span>
            ) : null}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      <div className="min-h-0 flex-1 p-3" data-testid="tab-panels">
        {tabs}
      </div>
    </RadixTabs.Root>
  );
}
