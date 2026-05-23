import type { ComponentPropsWithoutRef, ElementType, Ref } from "react";

export type ContainerSize = "sm" | "md" | "lg" | "xl" | "full";
export type ContainerPad = "none" | "sm" | "md" | "lg";

const SIZE_CLASS: Record<ContainerSize, string> = {
  sm: "max-w-screen-sm",
  md: "max-w-screen-md",
  lg: "max-w-screen-lg",
  xl: "max-w-screen-xl",
  full: "max-w-full",
};

const PAD_CLASS: Record<ContainerPad, string> = {
  none: "p-0",
  sm: "p-2",
  md: "p-4",
  lg: "p-6",
};

export interface ContainerProps
  extends Omit<ComponentPropsWithoutRef<"div">, "ref"> {
  size?: ContainerSize;
  pad?: ContainerPad;
  as?: ElementType;
  ref?: Ref<HTMLElement>;
}

/**
 * Centered layout container — applies a max-width and uniform
 * padding. Defaults: `size="lg"`, `pad="md"`. Use `as` to swap the
 * tag (e.g. `<Container as="section">`).
 */
export function Container(props: ContainerProps) {
  const {
    size = "lg",
    pad = "md",
    as,
    className = "",
    children,
    ref,
    ...rest
  } = props;
  const Tag = (as ?? "div") as ElementType;
  const cls =
    `mx-auto w-full ${SIZE_CLASS[size]} ${PAD_CLASS[pad]} ${className}`.trim();
  return (
    <Tag
      ref={ref}
      className={cls}
      data-container-size={size}
      data-container-pad={pad}
      {...rest}
    >
      {children}
    </Tag>
  );
}
