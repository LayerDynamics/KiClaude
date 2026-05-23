import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";

import { Text } from "./Text";

export type SidebarEdge = "left" | "right";

export interface SidebarProps
  extends Omit<ComponentPropsWithoutRef<"aside">, "title"> {
  /** Heading rendered in the sidebar header. */
  title?: ReactNode;
  /** Slot to the right of the title (e.g. open/close toggle). */
  actions?: ReactNode;
  /** Footer row rendered at the bottom (e.g. composer, status). */
  footer?: ReactNode;
  /** Which side of the viewport the sidebar lives on; controls
   *  border-side. */
  edge?: SidebarEdge;
  /** Width as a CSS length. Default `20rem`. */
  width?: number | string;
  /** When false, collapses the sidebar to its header. */
  open?: boolean;
  /** Drop body padding (used when the consumer manages its own
   *  scroll/padding regions). */
  flush?: boolean;
  ref?: Ref<HTMLElement>;
}

/**
 * Sidebar — a fixed-width vertical region with header / scrollable
 * body / footer. Used by the chat panel, the library browser, the
 * sheet navigator. The `edge` prop controls which side gets the
 * 1-px divider so the same component works on left or right.
 */
export function Sidebar(props: SidebarProps) {
  const {
    title,
    actions,
    footer,
    edge = "right",
    width = "20rem",
    open = true,
    flush = false,
    className = "",
    children,
    ref,
    ...rest
  } = props;

  const borderSide = edge === "right" ? "border-l" : "border-r";
  const bodyPad = flush ? "" : "p-3";
  const cls =
    `flex h-full flex-col bg-[var(--bg)] ${borderSide} border-[var(--border)] ${className}`.trim();
  const effectiveWidth = open ? width : "2.5rem";

  return (
    <aside
      ref={ref}
      data-testid="sidebar"
      data-edge={edge}
      data-open={open ? "true" : "false"}
      className={cls}
      style={{ width: effectiveWidth }}
      {...rest}
    >
      {title !== undefined || actions !== undefined ? (
        <header
          data-testid="sidebar-header"
          className="flex shrink-0 items-center justify-between gap-2 border-b border-[var(--border)] px-3 py-2"
        >
          <div className="min-w-0 flex-1">
            {typeof title === "string" ? (
              <Text variant="h4">{title}</Text>
            ) : (
              title
            )}
          </div>
          {actions !== undefined ? (
            <div data-testid="sidebar-actions" className="flex items-center gap-1">
              {actions}
            </div>
          ) : null}
        </header>
      ) : null}
      {open ? (
        <div
          data-testid="sidebar-body"
          className={`min-h-0 flex-1 overflow-auto ${bodyPad}`}
        >
          {children}
        </div>
      ) : null}
      {open && footer !== undefined ? (
        <div
          data-testid="sidebar-footer"
          className="shrink-0 border-t border-[var(--border)] p-2"
        >
          {footer}
        </div>
      ) : null}
    </aside>
  );
}
