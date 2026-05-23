import { useId } from "react";
import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";
import { Label as RadixLabel } from "radix-ui";

export type InputSize = "sm" | "md";
export type InputTone = "default" | "error";

const SIZE_CLASS: Record<InputSize, string> = {
  sm: "h-7 text-sm px-2",
  md: "h-9 text-base px-3",
};

const TONE_CLASS: Record<InputTone, string> = {
  default:
    "border-[var(--border)] focus:border-[var(--accent)] focus:ring-[var(--accent)]/30",
  error:
    "border-red-500/70 focus:border-red-500 focus:ring-red-500/30",
};

export interface InputProps
  extends Omit<ComponentPropsWithoutRef<"input">, "size"> {
  /** Optional label rendered above the field via Radix Label. */
  label?: ReactNode;
  /** Caption rendered under the field. Replaced by `error` when set. */
  hint?: ReactNode;
  /** Error message — turns the border red and renders below the field. */
  error?: ReactNode;
  /** Optional content rendered inside the field on the left (icon, prefix). */
  leadingAddon?: ReactNode;
  /** Optional content rendered inside the field on the right (unit, button). */
  trailingAddon?: ReactNode;
  inputSize?: InputSize;
  ref?: Ref<HTMLInputElement>;
}

/**
 * Text input with optional label / hint / error / addons. Always
 * uses `<Label>` from Radix when `label` is set so the `htmlFor`
 * wiring is correct even when the consumer doesn't pass an `id`.
 */
export function Input(props: InputProps) {
  const {
    label,
    hint,
    error,
    leadingAddon,
    trailingAddon,
    inputSize = "md",
    className = "",
    id,
    ref,
    ...rest
  } = props;
  const reactId = useId();
  const inputId = id ?? reactId;
  const tone: InputTone = error ? "error" : "default";

  const fieldCls =
    `w-full rounded-md border bg-[var(--bg)] text-[var(--text-h)] placeholder-[var(--text)]/60 outline-none transition-colors focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed ${SIZE_CLASS[inputSize]} ${TONE_CLASS[tone]}`.trim();

  const wrapperCls = leadingAddon || trailingAddon ? "relative" : "";

  return (
    <div className={`flex flex-col gap-1 ${className}`.trim()} data-tone={tone}>
      {label !== undefined ? (
        <RadixLabel.Root
          htmlFor={inputId}
          className="text-xs font-medium uppercase tracking-wide text-[var(--text)] opacity-80"
        >
          {label}
        </RadixLabel.Root>
      ) : null}
      <div className={wrapperCls}>
        {leadingAddon !== undefined ? (
          <span
            data-testid="input-leading"
            className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-2 text-[var(--text)]/70"
          >
            {leadingAddon}
          </span>
        ) : null}
        <input
          id={inputId}
          ref={ref}
          aria-invalid={error ? true : undefined}
          aria-describedby={
            error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined
          }
          className={`${fieldCls} ${leadingAddon ? "pl-8" : ""} ${trailingAddon ? "pr-8" : ""}`.trim()}
          {...rest}
        />
        {trailingAddon !== undefined ? (
          <span
            data-testid="input-trailing"
            className="absolute inset-y-0 right-0 flex items-center pr-2 text-[var(--text)]/70"
          >
            {trailingAddon}
          </span>
        ) : null}
      </div>
      {error !== undefined ? (
        <p
          id={`${inputId}-error`}
          data-testid="input-error"
          className="text-xs text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      ) : hint !== undefined ? (
        <p
          id={`${inputId}-hint`}
          data-testid="input-hint"
          className="text-xs text-[var(--text)]/70"
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}
