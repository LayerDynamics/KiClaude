import {
  Children,
  isValidElement,
  type ReactElement,
  type ReactNode,
  type Ref,
} from "react";
import { Tabs as RadixTabs } from "radix-ui";

import { Tab, type TabProps } from "./Tab";

export type TabSidebarEdge = "left" | "right";

export interface TabSidebarProps {
  children: ReactNode;
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  /** Which edge the tab strip sits on — controls whether the
   *  divider is on the right (strip on left) or left (strip on
   *  right). Default `left`. */
  edge?: TabSidebarEdge;
  /** Width of the tab strip. Default `12rem`. */
  stripWidth?: number | string;
  className?: string;
  ref?: Ref<HTMLDivElement>;
}

function extractTabs(children: ReactNode): ReactElement<TabProps>[] {
  const tabs: ReactElement<TabProps>[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    if (child.type !== Tab) {
      if (typeof console !== "undefined") {
        console.warn("TabSidebar: ignoring non-Tab child.");
      }
      return;
    }
    tabs.push(child as ReactElement<TabProps>);
  });
  return tabs;
}

/**
 * TabSidebar — a vertical Radix `Tabs.Root` where the trigger
 * column is a labelled sidebar and the active panel fills the
 * remaining area. Used in the BOM / DRC / Fab tool docks where the
 * user picks a tool from a left strip and the panel content opens
 * to the right.
 */
export function TabSidebar(props: TabSidebarProps) {
  const {
    children,
    value,
    defaultValue,
    onValueChange,
    edge = "left",
    stripWidth = "12rem",
    className = "",
    ref,
  } = props;

  const tabs = extractTabs(children);
  const fallback = tabs.find((t) => !t.props.disabled)?.props.value;
  const effectiveDefault = defaultValue ?? fallback;

  const rootCls = `flex h-full ${edge === "right" ? "flex-row-reverse" : "flex-row"} ${className}`.trim();
  const stripCls = [
    "flex h-full shrink-0 flex-col gap-1 overflow-auto bg-[var(--bg)] p-2",
    edge === "left" ? "border-r border-[var(--border)]" : "border-l border-[var(--border)]",
  ].join(" ");
  const triggerCls = [
    "flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm font-medium text-[var(--text)]",
    "transition-colors hover:bg-[var(--code-bg)] hover:text-[var(--text-h)]",
    "data-[state=active]:bg-[var(--accent-bg)] data-[state=active]:text-[var(--accent)]",
    "data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50",
    "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]/40",
  ].join(" ");
  const panelsCls = "min-w-0 flex-1 overflow-auto p-3";

  return (
    <RadixTabs.Root
      ref={ref}
      value={value}
      defaultValue={effectiveDefault}
      onValueChange={onValueChange}
      orientation="vertical"
      className={rootCls}
      data-testid="tab-sidebar"
      data-edge={edge}
    >
      <RadixTabs.List
        aria-orientation="vertical"
        className={stripCls}
        style={{ width: stripWidth }}
        data-testid="tab-sidebar-list"
      >
        {tabs.map((tab) => (
          <RadixTabs.Trigger
            key={tab.props.value}
            value={tab.props.value}
            disabled={tab.props.disabled}
            data-testid={`tab-sidebar-trigger-${tab.props.value}`}
            className={triggerCls}
          >
            <span className="min-w-0 flex-1 truncate">{tab.props.label}</span>
            {tab.props.accessory !== undefined ? (
              <span className="shrink-0 text-xs text-[var(--text)]/70">
                {tab.props.accessory}
              </span>
            ) : null}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      <div className={panelsCls} data-testid="tab-sidebar-panels">
        {tabs}
      </div>
    </RadixTabs.Root>
  );
}
