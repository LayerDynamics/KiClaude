import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";

export interface MenuItem {
  id: string;
  label: ReactNode;
  /** Optional secondary line under the label. */
  hint?: ReactNode;
  /** Optional icon node rendered before the label. */
  icon?: ReactNode;
  /** Optional right-aligned accessory (badge, count). */
  accessory?: ReactNode;
  /** Optional shortcut hint rendered on the right. */
  shortcut?: string;
  disabled?: boolean;
  onSelect?: () => void;
}

export interface MenuProps
  extends Omit<ComponentPropsWithoutRef<"ul">, "ref" | "onSelect"> {
  items: MenuItem[];
  /** Currently-selected item id (highlighted). */
  activeId?: string;
  /** Fires when any non-disabled item is clicked or activated via
   *  keyboard. Passes the item's id. Composes with the per-item
   *  `onSelect`. */
  onSelect?: (id: string) => void;
  /** Tightens row padding. Default false. */
  compact?: boolean;
  ref?: Ref<HTMLUListElement>;
}

/**
 * Menu — a non-floating list of selectable rows. Used for the sheet
 * tree, the library item list, the tool palette, command-bar
 * results. For a floating menu (button-triggered), see `Dropdown`.
 *
 * Keyboard support: each row is a real `<button>` so Tab navigates
 * naturally and Enter/Space activates `onSelect`.
 */
export function Menu(props: MenuProps) {
  const {
    items,
    activeId,
    onSelect,
    compact = false,
    className = "",
    ref,
    ...rest
  } = props;

  const rowPad = compact ? "px-2 py-1" : "px-3 py-2";
  const cls =
    `m-0 flex list-none flex-col gap-0 p-0 ${className}`.trim();

  return (
    <ul ref={ref} role="menu" className={cls} {...rest}>
      {items.map((item) => {
        const isActive = item.id === activeId;
        const rowCls = [
          "flex w-full items-center gap-2 rounded-sm text-left text-sm",
          rowPad,
          isActive
            ? "bg-[var(--accent-bg)] text-[var(--accent)]"
            : "text-[var(--text-h)] hover:bg-[var(--code-bg)]",
          item.disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer",
        ].join(" ");

        return (
          <li key={item.id} role="none">
            <button
              type="button"
              role="menuitem"
              data-testid={`menu-item-${item.id}`}
              data-active={isActive ? "true" : undefined}
              disabled={item.disabled}
              className={rowCls}
              onClick={() => {
                if (item.disabled) return;
                item.onSelect?.();
                onSelect?.(item.id);
              }}
            >
              {item.icon !== undefined ? (
                <span className="shrink-0 text-[var(--text)]/80">
                  {item.icon}
                </span>
              ) : null}
              <span className="min-w-0 flex-1">
                <span className="block truncate">{item.label}</span>
                {item.hint !== undefined ? (
                  <span className="block truncate text-xs text-[var(--text)]/70">
                    {item.hint}
                  </span>
                ) : null}
              </span>
              {item.accessory !== undefined ? (
                <span className="shrink-0 text-xs text-[var(--text)]/70">
                  {item.accessory}
                </span>
              ) : null}
              {item.shortcut !== undefined ? (
                <span className="shrink-0 text-xs text-[var(--text)]/60">
                  {item.shortcut}
                </span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
