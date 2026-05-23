import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";

export type CardTone = "default" | "muted" | "accent" | "danger";

const TONE_CLASS: Record<CardTone, string> = {
  default: "border-[var(--border)] bg-[var(--bg)]",
  muted: "border-[var(--border)] bg-[var(--code-bg)]",
  accent: "border-[var(--accent-border)] bg-[var(--accent-bg)]",
  danger: "border-red-400/50 bg-red-50/40 dark:bg-red-950/30",
};

export interface CardProps extends Omit<ComponentPropsWithoutRef<"div">, "ref"> {
  tone?: CardTone;
  /** Optional rendered header — when supplied, gets a divider below. */
  header?: ReactNode;
  /** Optional rendered footer — when supplied, gets a divider above. */
  footer?: ReactNode;
  /** Drop the default `p-4` body padding (use when wrapping a custom
   *  inner layout). */
  flush?: boolean;
  ref?: Ref<HTMLDivElement>;
}

/**
 * Generic boxed-content container with optional header/footer slots.
 * Carries a 1-px border, rounded corners, and tonal background. Body
 * content lives in `children`; pass `flush` to disable inner padding.
 */
export function Card(props: CardProps) {
  const {
    tone = "default",
    header,
    footer,
    flush = false,
    className = "",
    children,
    ref,
    ...rest
  } = props;
  const cls =
    `flex flex-col rounded-lg border ${TONE_CLASS[tone]} shadow-[var(--shadow)] ${className}`.trim();
  return (
    <div ref={ref} className={cls} data-tone={tone} {...rest}>
      {header !== undefined ? (
        <div
          data-testid="card-header"
          className="border-b border-[var(--border)] px-4 py-2"
        >
          {header}
        </div>
      ) : null}
      <div
        data-testid="card-body"
        className={flush ? "" : "p-4"}
      >
        {children}
      </div>
      {footer !== undefined ? (
        <div
          data-testid="card-footer"
          className="border-t border-[var(--border)] px-4 py-2"
        >
          {footer}
        </div>
      ) : null}
    </div>
  );
}
