import { useEffect, useId, useRef } from "react";
import type { ComponentPropsWithoutRef, ReactNode, Ref } from "react";
import { Label as RadixLabel } from "radix-ui";

export type InputParagraphTone = "default" | "error";

const TONE_CLASS: Record<InputParagraphTone, string> = {
  default:
    "border-[var(--border)] focus:border-[var(--accent)] focus:ring-[var(--accent)]/30",
  error:
    "border-red-500/70 focus:border-red-500 focus:ring-red-500/30",
};

export interface InputParagraphProps
  extends ComponentPropsWithoutRef<"textarea"> {
  label?: ReactNode;
  hint?: ReactNode;
  error?: ReactNode;
  /** Min visible rows. Default 3. */
  minRows?: number;
  /** Max visible rows before scrolling kicks in. Default 10. */
  maxRows?: number;
  /** When true, the textarea grows to fit its content (between
   *  `minRows` and `maxRows`). Default true. */
  autoResize?: boolean;
  ref?: Ref<HTMLTextAreaElement>;
}

/**
 * Multi-line text input — the chat composer, the activity-journal
 * comment box, free-form `description` fields. Auto-resizes between
 * `minRows` and `maxRows` so the user gets a comfortable target on
 * short prompts and a scrollable region on long ones.
 */
export function InputParagraph(props: InputParagraphProps) {
  const {
    label,
    hint,
    error,
    minRows = 3,
    maxRows = 10,
    autoResize = true,
    className = "",
    id,
    value,
    defaultValue,
    ref: externalRef,
    onInput,
    ...rest
  } = props;
  const reactId = useId();
  const inputId = id ?? reactId;
  const tone: InputParagraphTone = error ? "error" : "default";
  const internalRef = useRef<HTMLTextAreaElement | null>(null);

  function setRef(node: HTMLTextAreaElement | null) {
    internalRef.current = node;
    if (typeof externalRef === "function") externalRef(node);
    else if (externalRef && typeof externalRef === "object")
      (externalRef as { current: HTMLTextAreaElement | null }).current = node;
  }

  function resize() {
    if (!autoResize) return;
    const node = internalRef.current;
    if (!node) return;
    const style = window.getComputedStyle(node);
    const lineHeight = parseFloat(style.lineHeight || "20") || 20;
    const paddingY =
      parseFloat(style.paddingTop || "0") + parseFloat(style.paddingBottom || "0");
    const minH = lineHeight * minRows + paddingY;
    const maxH = lineHeight * maxRows + paddingY;
    node.style.height = "auto";
    const next = Math.min(Math.max(node.scrollHeight, minH), maxH);
    node.style.height = `${next}px`;
    node.style.overflowY = node.scrollHeight > maxH ? "auto" : "hidden";
  }

  useEffect(() => {
    resize();
    // resize() depends only on minRows / maxRows / autoResize / value,
    // not on its own identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, minRows, maxRows, autoResize]);

  const fieldCls =
    `w-full resize-none rounded-md border bg-[var(--bg)] text-[var(--text-h)] placeholder-[var(--text)]/60 px-3 py-2 text-base leading-relaxed outline-none transition-colors focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed ${TONE_CLASS[tone]}`.trim();

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
      <textarea
        id={inputId}
        ref={setRef}
        rows={minRows}
        value={value}
        defaultValue={defaultValue}
        aria-invalid={error ? true : undefined}
        aria-describedby={
          error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined
        }
        className={fieldCls}
        onInput={(e) => {
          resize();
          onInput?.(e);
        }}
        {...rest}
      />
      {error !== undefined ? (
        <p
          id={`${inputId}-error`}
          data-testid="input-paragraph-error"
          className="text-xs text-red-600 dark:text-red-400"
        >
          {error}
        </p>
      ) : hint !== undefined ? (
        <p
          id={`${inputId}-hint`}
          data-testid="input-paragraph-hint"
          className="text-xs text-[var(--text)]/70"
        >
          {hint}
        </p>
      ) : null}
    </div>
  );
}
