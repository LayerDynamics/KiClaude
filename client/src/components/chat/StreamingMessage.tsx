export interface StreamingMessageProps {
  /** Stable message id. */
  id: string;
  /** "user" | "assistant" | "system". */
  role: "user" | "assistant" | "system";
  /** Accumulated text content. */
  content: string;
  /** True while the assistant is still streaming tokens. */
  streaming: boolean;
  /** ISO-8601 timestamp. */
  ts?: string;
}

const ROLE_BG: Record<string, string> = {
  user: "bg-slate-100 dark:bg-slate-800",
  assistant: "bg-[var(--bg)]",
  system: "bg-[var(--accent-bg)]",
};

const ROLE_HEADER_COLOR: Record<string, string> = {
  user: "text-sky-700 dark:text-sky-300",
  assistant: "text-emerald-700 dark:text-emerald-300",
  system: "text-[var(--accent)]",
};

/**
 * M1-T-07 sub-component: renders one message in the chat transcript.
 * While the assistant is mid-stream, a blinking cursor is appended
 * so the user sees the token flow live. Kept as an `<li>` because
 * the parent transcript is a `<ul>`.
 */
export function StreamingMessage(props: StreamingMessageProps) {
  const { id, role, content, streaming, ts } = props;
  return (
    <li
      data-testid={`chat-msg-${role}`}
      data-id={id}
      data-streaming={streaming ? "true" : "false"}
      className={`mb-1.5 list-none rounded-md border border-[var(--border)] p-2 text-[var(--text-h)] ${ROLE_BG[role] ?? ROLE_BG.assistant}`}
    >
      <header
        className={`mb-1 flex justify-between text-[11px] uppercase tracking-wide ${ROLE_HEADER_COLOR[role] ?? ROLE_HEADER_COLOR.assistant}`}
      >
        <span className="font-semibold">{role}</span>
        {ts ? (
          <time
            dateTime={ts}
            className="text-[10px] text-[var(--text)]/60"
          >
            {shortTs(ts)}
          </time>
        ) : null}
      </header>
      <div className="text-sm break-words whitespace-pre-wrap">
        {content}
        {streaming ? (
          <span
            data-testid="streaming-cursor"
            aria-hidden="true"
            className="ml-0.5 inline-block animate-pulse"
          >
            ▌
          </span>
        ) : null}
      </div>
    </li>
  );
}

function shortTs(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString();
  } catch {
    return iso;
  }
}
