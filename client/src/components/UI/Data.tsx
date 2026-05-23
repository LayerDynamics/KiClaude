import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";

export interface DataItem {
  key: string;
  label: ReactNode;
  value: ReactNode;
  /** Optional secondary line under the value (e.g. unit, source). */
  hint?: ReactNode;
  /** Right-aligned action slot (e.g. copy button). */
  action?: ReactNode;
}

export type DataLayout = "rows" | "grid";

export interface DataProps extends Omit<ComponentPropsWithoutRef<"dl">, "ref"> {
  items: DataItem[];
  /** `rows` lays out label-above-value (compact); `grid` is two
   *  columns (label | value). Default `grid`. */
  layout?: DataLayout;
  /** Width of the label column when `layout="grid"`. Default
   *  `10rem`. */
  labelWidth?: string;
  /** Optional empty-state node when `items` is `[]`. */
  empty?: ReactNode;
  ref?: Ref<HTMLDListElement>;
}

/**
 * Key/value definition list — used for property inspectors, BOM
 * line summaries, "About" cards, etc. Renders semantic `<dl>` /
 * `<dt>` / `<dd>` so screen readers announce relationships, with a
 * tailwind grid for layout when `layout="grid"`.
 */
export function Data(props: DataProps) {
  const {
    items,
    layout = "grid",
    labelWidth = "10rem",
    empty,
    className = "",
    ref,
    ...rest
  } = props;

  if (items.length === 0) {
    return (
      <div
        data-testid="data-empty"
        className={`rounded-md border border-dashed border-[var(--border)] px-3 py-4 text-center text-sm text-[var(--text)]/70 ${className}`.trim()}
      >
        {empty ?? "No data"}
      </div>
    );
  }

  const cls =
    `flex flex-col divide-y divide-[var(--border)] rounded-md border border-[var(--border)] bg-[var(--bg)] ${className}`.trim();

  return (
    <dl ref={ref} data-layout={layout} className={cls} {...rest}>
      {items.map((item) => (
        <div
          key={item.key}
          data-testid={`data-row-${item.key}`}
          className={
            layout === "grid"
              ? "grid items-baseline gap-3 px-3 py-2"
              : "flex flex-col gap-0.5 px-3 py-2"
          }
          style={
            layout === "grid"
              ? { gridTemplateColumns: `${labelWidth} 1fr auto` }
              : undefined
          }
        >
          <dt
            data-testid={`data-label-${item.key}`}
            className="min-w-0 text-xs font-medium uppercase tracking-wide text-[var(--text)] opacity-80"
          >
            {item.label}
          </dt>
          <dd
            data-testid={`data-value-${item.key}`}
            className="min-w-0 text-sm text-[var(--text-h)]"
          >
            {item.value}
            {item.hint !== undefined ? (
              <div className="mt-0.5 text-xs text-[var(--text)]/70">
                {item.hint}
              </div>
            ) : null}
          </dd>
          {layout === "grid" && item.action !== undefined ? (
            <div
              data-testid={`data-action-${item.key}`}
              className="flex items-center justify-end"
            >
              {item.action}
            </div>
          ) : null}
        </div>
      ))}
    </dl>
  );
}
