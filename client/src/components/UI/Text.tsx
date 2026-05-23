import type { ComponentPropsWithoutRef, ElementType, Ref } from "react";

export type TextVariant =
  | "h1"
  | "h2"
  | "h3"
  | "h4"
  | "body"
  | "small"
  | "caption"
  | "mono"
  | "label";

const VARIANT_TAG: Record<TextVariant, ElementType> = {
  h1: "h1",
  h2: "h2",
  h3: "h3",
  h4: "h4",
  body: "p",
  small: "p",
  caption: "span",
  mono: "code",
  label: "label",
};

const VARIANT_CLASS: Record<TextVariant, string> = {
  h1: "text-4xl font-medium leading-tight tracking-tight text-[var(--text-h)]",
  h2: "text-2xl font-medium leading-snug tracking-tight text-[var(--text-h)]",
  h3: "text-lg font-medium leading-snug text-[var(--text-h)]",
  h4: "text-base font-semibold leading-snug text-[var(--text-h)]",
  body: "text-base leading-relaxed text-[var(--text)]",
  small: "text-sm leading-normal text-[var(--text)]",
  caption: "text-xs leading-normal text-[var(--text)] opacity-70",
  mono: "font-mono text-sm rounded px-1.5 py-0.5 bg-[var(--code-bg)] text-[var(--text-h)]",
  label: "text-xs font-medium uppercase tracking-wide text-[var(--text)] opacity-80",
};

export interface TextProps extends Omit<ComponentPropsWithoutRef<"p">, "ref"> {
  variant?: TextVariant;
  as?: ElementType;
  ref?: Ref<HTMLElement>;
}

/**
 * Typography primitive — renders the semantic tag matching the
 * variant (`h1`–`h4`, `p`, `span`, `code`, `label`) and applies the
 * variant's tailwind classes. Override the tag with `as`; merge
 * extra classes with `className`.
 */
export function Text(props: TextProps) {
  const { variant = "body", as, className = "", children, ref, ...rest } = props;
  const Tag = (as ?? VARIANT_TAG[variant]) as ElementType;
  const cls = `${VARIANT_CLASS[variant]} ${className}`.trim();
  return (
    <Tag ref={ref} className={cls} data-variant={variant} {...rest}>
      {children}
    </Tag>
  );
}
