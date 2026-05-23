import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";

import { Text } from "./Text";

export type PanelDensity = "comfortable" | "compact";

export interface PanelProps
  extends Omit<ComponentPropsWithoutRef<"section">, "title"> {
  /** Title rendered in the panel header. Accepts a string (rendered
   *  via `<Text variant="h4">`) or any ReactNode for custom markup. */
  title?: ReactNode;
  /** Optional descriptive subtitle under the title. */
  subtitle?: ReactNode;
  /** Slot rendered to the right of the title (e.g. close button, badge). */
  actions?: ReactNode;
  /** When true, drops vertical padding around the body. */
  flush?: boolean;
  /** Compact density tightens header/body padding. */
  density?: PanelDensity;
  /** When set, the body becomes scrollable with this max height. */
  maxBodyHeight?: number | string;
  ref?: Ref<HTMLElement>;
}

/**
 * Panel — a labeled side-panel container with a header bar and
 * optional actions slot. Used wherever a feature area needs a
 * stable, titled box (property panel, ERC panel, BOM sidebar, etc.).
 */
export function Panel(props: PanelProps) {
  const {
    title,
    subtitle,
    actions,
    flush = false,
    density = "comfortable",
    maxBodyHeight,
    className = "",
    children,
    ref,
    ...rest
  } = props;

  const headerPad = density === "compact" ? "px-3 py-1.5" : "px-4 py-3";
  const bodyPad = flush ? "" : density === "compact" ? "p-3" : "p-4";
  const cls =
    `flex flex-col rounded-md border border-[var(--border)] bg-[var(--bg)] ${className}`.trim();

  return (
    <section ref={ref} data-density={density} className={cls} {...rest}>
      {title !== undefined || actions !== undefined ? (
        <header
          data-testid="panel-header"
          className={`flex items-center justify-between gap-3 border-b border-[var(--border)] ${headerPad}`}
        >
          <div className="min-w-0 flex-1">
            {typeof title === "string" ? (
              <Text variant="h4">{title}</Text>
            ) : (
              title
            )}
            {subtitle !== undefined ? (
              <div className="mt-0.5">
                {typeof subtitle === "string" ? (
                  <Text variant="caption">{subtitle}</Text>
                ) : (
                  subtitle
                )}
              </div>
            ) : null}
          </div>
          {actions !== undefined ? (
            <div data-testid="panel-actions" className="flex items-center gap-1">
              {actions}
            </div>
          ) : null}
        </header>
      ) : null}
      <div
        data-testid="panel-body"
        className={`min-h-0 flex-1 ${bodyPad} ${maxBodyHeight !== undefined ? "overflow-auto" : ""}`}
        style={maxBodyHeight !== undefined ? { maxHeight: maxBodyHeight } : undefined}
      >
        {children}
      </div>
    </section>
  );
}
