import type { ReactNode, Ref } from "react";
import { Tabs as RadixTabs } from "radix-ui";

export interface TabProps {
  /** Stable identifier used by `TabGroup` to match trigger ↔ content. */
  value: string;
  /** Visible label rendered in the trigger row. */
  label: ReactNode;
  /** Tab body. */
  children: ReactNode;
  /** Optional accessory rendered to the right of the label (badge,
   *  count, icon). */
  accessory?: ReactNode;
  disabled?: boolean;
  className?: string;
  /** When true, the body is kept mounted while inactive (Radix's
   *  `forceMount`). Useful for forms that lose unsaved state when
   *  unmounted. */
  keepMounted?: boolean;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Tab — declarative child of a `TabGroup`. Carries a `value`, label,
 * and body. `TabGroup` reads the props off each child via `React.Children`
 * to build the Radix `Tabs.List` + `Tabs.Trigger` + `Tabs.Content`
 * trees. A `<Tab>` rendered outside a `<TabGroup>` renders only its
 * body content (defensive — Radix throws if asked to render
 * `Tabs.Content` outside a `Tabs.Root`).
 */
export function Tab(props: TabProps) {
  const { value, children, keepMounted, className = "", ref } = props;
  return (
    <RadixTabs.Content
      ref={ref}
      value={value}
      forceMount={keepMounted ? true : undefined}
      data-testid={`tab-content-${value}`}
      className={`outline-none data-[state=inactive]:hidden focus-visible:outline-none ${className}`.trim()}
    >
      {children}
    </RadixTabs.Content>
  );
}
