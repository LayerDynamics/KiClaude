import type { ReactNode, Ref } from "react";
import { DropdownMenu as RadixDropdownMenu } from "radix-ui";

export type DropdownItem =
  | { kind: "item"; id: string; label: ReactNode; onSelect?: () => void; disabled?: boolean; danger?: boolean; shortcut?: string }
  | { kind: "separator"; id: string }
  | { kind: "label"; id: string; label: ReactNode };

export interface DropdownProps {
  /** Element rendered as the menu trigger. */
  trigger: ReactNode;
  items: DropdownItem[];
  /** Where the menu opens relative to the trigger. */
  align?: "start" | "center" | "end";
  /** Distance in px between the trigger and the menu. */
  sideOffset?: number;
  className?: string;
  /** Controlled open state. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  ref?: Ref<HTMLButtonElement>;
}

/**
 * Dropdown — Radix `DropdownMenu` driven by a flat `items` array.
 * Supports labels, separators, and selectable items with optional
 * `danger` styling and `shortcut` hint text. Pass the trigger
 * element via the `trigger` prop.
 */
export function Dropdown(props: DropdownProps) {
  const {
    trigger,
    items,
    align = "start",
    sideOffset = 6,
    className = "",
    open,
    onOpenChange,
    ref,
  } = props;

  const contentCls =
    `z-50 min-w-[10rem] overflow-hidden rounded-md border border-[var(--border)] bg-[var(--bg)] p-1 shadow-[var(--shadow)] ${className}`.trim();
  const itemBaseCls =
    "flex select-none items-center justify-between gap-3 rounded-sm px-2 py-1.5 text-sm text-[var(--text-h)] outline-none data-[highlighted]:bg-[var(--accent-bg)] data-[highlighted]:text-[var(--accent)] data-[disabled]:cursor-not-allowed data-[disabled]:opacity-50";

  return (
    <RadixDropdownMenu.Root open={open} onOpenChange={onOpenChange}>
      <RadixDropdownMenu.Trigger asChild ref={ref}>
        {trigger}
      </RadixDropdownMenu.Trigger>
      <RadixDropdownMenu.Portal>
        <RadixDropdownMenu.Content
          align={align}
          sideOffset={sideOffset}
          className={contentCls}
          data-testid="dropdown-content"
        >
          {items.map((item) => {
            if (item.kind === "separator") {
              return (
                <RadixDropdownMenu.Separator
                  key={item.id}
                  data-testid={`dropdown-separator-${item.id}`}
                  className="my-1 h-px bg-[var(--border)]"
                />
              );
            }
            if (item.kind === "label") {
              return (
                <RadixDropdownMenu.Label
                  key={item.id}
                  data-testid={`dropdown-label-${item.id}`}
                  className="px-2 py-1 text-xs font-medium uppercase tracking-wide text-[var(--text)]/70"
                >
                  {item.label}
                </RadixDropdownMenu.Label>
              );
            }
            return (
              <RadixDropdownMenu.Item
                key={item.id}
                data-testid={`dropdown-item-${item.id}`}
                disabled={item.disabled}
                onSelect={item.onSelect}
                className={`${itemBaseCls} ${item.danger ? "text-red-600 dark:text-red-400 data-[highlighted]:bg-red-100 dark:data-[highlighted]:bg-red-950" : ""}`.trim()}
              >
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                {item.shortcut !== undefined ? (
                  <span className="text-xs text-[var(--text)]/60">
                    {item.shortcut}
                  </span>
                ) : null}
              </RadixDropdownMenu.Item>
            );
          })}
        </RadixDropdownMenu.Content>
      </RadixDropdownMenu.Portal>
    </RadixDropdownMenu.Root>
  );
}
