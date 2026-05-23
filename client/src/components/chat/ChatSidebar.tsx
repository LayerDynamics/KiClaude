import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { KiclaudeWsClient, type KiclaudeWsListener } from "../../lib/ws";
import { useChatStore, type ChatMessage } from "../../stores/chatStore";
import { InputParagraph, Sidebar, Text } from "../UI";

import { AskUserQuestionCard, type AskUserQuestion } from "./AskUserQuestionCard";
import { StreamingMessage } from "./StreamingMessage";
import { ToolCallCard, type ToolCallRecord } from "./ToolCallCard";

export interface ChatSidebarProps {
  /** Override the WS client — tests inject a stub. */
  client?: KiclaudeWsClient;
  /** Optional callback fired when the user submits a prompt. */
  onSend?: (prompt: string) => void;
  /** Whether the sidebar starts open. Persisted to chatStore.status
   * for follow-up M0-T-03 e2e checks. */
  initiallyOpen?: boolean;
}

/**
 * kiclaude chat sidebar. Streams assistant tokens into the message
 * list via the [`chatStore`](../../stores/chatStore.ts). Closing and
 * re-opening preserves history (the store persists to localStorage).
 */
export function ChatSidebar(props: ChatSidebarProps = {}) {
  const messages = useChatStore((s) => s.messages);
  const status = useChatStore((s) => s.status);
  const sendMessage = useChatStore((s) => s.send);
  const appendToken = useChatStore((s) => s.appendAssistantToken);
  const finalizeAssistant = useChatStore((s) => s.finalizeAssistant);
  const setStatus = useChatStore((s) => s.setStatus);

  const [open, setOpen] = useState(props.initiallyOpen ?? true);
  const [draft, setDraft] = useState("");
  const [toolCalls, setToolCalls] = useState<ToolCallRecord[]>([]);
  const [questions, setQuestions] = useState<AskUserQuestion[]>([]);
  const [answeredQuestions, setAnsweredQuestions] = useState<
    Record<string, { picks: string[]; notes: string }>
  >({});
  const wsRef = useRef<KiclaudeWsClient | null>(null);

  // Use the injected client (tests) or build a default one once.
  const client = useMemo(() => {
    if (props.client) return props.client;
    return new KiclaudeWsClient();
  }, [props.client]);

  useEffect(() => {
    wsRef.current = client;
    setStatus("connecting");
    const listener: KiclaudeWsListener = (event) => {
      if (event.kind === "open") setStatus("connected");
      if (event.kind === "close") setStatus("disconnected");
      if (event.kind === "error") setStatus("error", event.error);
      if (event.kind === "json" && typeof event.data === "object" && event.data !== null) {
        handleEvent(event.data as Record<string, unknown>);
      }
    };
    const unsubscribe = client.subscribe(listener);
    client.connect();
    return () => {
      unsubscribe();
      // Don't close the client — it may be reused by tests. The next
      // owner is responsible for `close()`.
    };

    function handleEvent(payload: Record<string, unknown>): void {
      const kind = payload.kind;
      if (kind === "assistant_token" && typeof payload.id === "string" && typeof payload.token === "string") {
        appendToken(payload.id, payload.token);
      } else if (kind === "assistant_end" && typeof payload.id === "string") {
        finalizeAssistant(payload.id);
      } else if (kind === "tool_use_start") {
        appendToolCall(payload);
      } else if (kind === "tool_use_end") {
        finalizeToolCall(payload);
      } else if (kind === "ask_user_question") {
        appendQuestion(payload);
      }
    }

    function appendToolCall(payload: Record<string, unknown>): void {
      const id = String(payload.id ?? "");
      if (!id) return;
      const record: ToolCallRecord = {
        id,
        tool_name: String(payload.tool_name ?? ""),
        input: (payload.input as Record<string, unknown>) ?? {},
        output: null,
        in_flight: true,
      };
      setToolCalls((prev) => {
        if (prev.some((c) => c.id === id)) return prev;
        return [...prev, record];
      });
    }

    function finalizeToolCall(payload: Record<string, unknown>): void {
      const id = String(payload.id ?? "");
      if (!id) return;
      setToolCalls((prev) =>
        prev.map((c) =>
          c.id === id
            ? {
                ...c,
                output: (payload.output as Record<string, unknown>) ?? {},
                duration_ms:
                  typeof payload.duration_ms === "number"
                    ? payload.duration_ms
                    : null,
                error: payload.ok === false || payload.isError === true,
                in_flight: false,
              }
            : c,
        ),
      );
    }

    function appendQuestion(payload: Record<string, unknown>): void {
      const id = String(payload.id ?? "");
      if (!id) return;
      const optionsRaw = (payload.options as unknown[]) ?? [];
      const options = optionsRaw
        .filter((o): o is Record<string, unknown> => typeof o === "object" && o !== null)
        .map((o) => ({
          label: String(o.label ?? ""),
          description:
            typeof o.description === "string" ? o.description : undefined,
        }));
      const question: AskUserQuestion = {
        id,
        question: String(payload.question ?? ""),
        header: typeof payload.header === "string" ? payload.header : undefined,
        options,
        multiSelect: payload.multiSelect === true,
      };
      setQuestions((prev) => {
        if (prev.some((q) => q.id === id)) return prev;
        return [...prev, question];
      });
    }
  }, [client, appendToken, finalizeAssistant, setStatus]);

  const onAnswerQuestion = useCallback(
    (questionId: string, answer: { picks: string[]; notes: string }) => {
      setAnsweredQuestions((prev) => ({ ...prev, [questionId]: answer }));
      wsRef.current?.send({
        kind: "ask_user_question_answer",
        id: questionId,
        picks: answer.picks,
        notes: answer.notes,
      });
    },
    [],
  );

  function submit(): void {
    if (!draft.trim()) return;
    sendMessage(draft);
    wsRef.current?.send({ kind: "user_prompt", content: draft });
    if (props.onSend) props.onSend(draft);
    setDraft("");
  }

  if (!open) {
    return (
      <button
        data-testid="chat-sidebar-open"
        onClick={() => setOpen(true)}
        type="button"
        className="inline-flex h-8 items-center rounded-md border border-[var(--border)] bg-[var(--bg)] px-3 text-sm text-[var(--text-h)] hover:bg-[var(--code-bg)]"
      >
        Open chat
      </button>
    );
  }

  return (
    <Sidebar
      data-testid="chat-sidebar"
      aria-label="kiclaude chat"
      edge="right"
      width="22.5rem"
      open
      flush
      className="max-h-[70vh]"
      title={
        <div data-testid="chat-sidebar-header" className="flex items-baseline gap-2">
          <Text variant="h4">kiclaude</Text>
          <span
            data-testid="chat-status"
            className={`text-xs ${statusColor(status)}`}
          >
            {status}
          </span>
        </div>
      }
      actions={
        <button
          data-testid="chat-sidebar-close"
          type="button"
          onClick={() => setOpen(false)}
          aria-label="close"
          className="rounded p-1 text-lg leading-none text-[var(--text)]/70 hover:bg-[var(--code-bg)] hover:text-[var(--text-h)]"
        >
          ×
        </button>
      }
      footer={
        <form
          data-testid="chat-form"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
          className="flex items-end gap-2"
        >
          <div className="flex-1">
            <InputParagraph
              data-testid="chat-input"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Ask kiclaude…"
              minRows={1}
              maxRows={6}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  submit();
                }
              }}
            />
          </div>
          <button
            data-testid="chat-send"
            type="submit"
            className="inline-flex h-9 shrink-0 items-center rounded-md bg-[var(--accent)] px-3 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
          >
            Send
          </button>
        </form>
      }
    >
      <div className="flex h-full min-h-0 flex-col gap-2 p-3">
        <ul
          data-testid="chat-messages"
          className="m-0 min-h-0 flex-1 list-none overflow-y-auto p-0"
        >
          {messages.map((m) => (
            <StreamingMessage
              key={m.id}
              id={m.id}
              role={m.role}
              content={m.content}
              streaming={Boolean(m.streaming)}
              ts={m.ts}
            />
          ))}
        </ul>
        {toolCalls.length > 0 ? (
          <div
            data-testid="chat-tool-calls"
            className="flex flex-col gap-1 border-t border-[var(--border)] pt-2"
          >
            {toolCalls.map((call) => (
              <ToolCallCard key={call.id} call={call} />
            ))}
          </div>
        ) : null}
        {questions.length > 0 ? (
          <div
            data-testid="chat-questions"
            className="flex flex-col gap-1 border-t border-[var(--border)] pt-2"
          >
            {questions.map((q) => {
              const answer = answeredQuestions[q.id];
              return (
                <AskUserQuestionCard
                  key={q.id}
                  question={q}
                  answered={Boolean(answer)}
                  preselected={undefined}
                  preselectedNotes={answer?.notes}
                  onAnswer={(a) => onAnswerQuestion(q.id, a)}
                />
              );
            })}
          </div>
        ) : null}
      </div>
    </Sidebar>
  );
}

// `ChatMessage` is now consumed by `StreamingMessage`; reference the
// type once here to keep the import side-effect visible to tooling.
const _ChatMessageType: ChatMessage | undefined = undefined;
void _ChatMessageType;

function statusColor(status: string): string {
  if (status === "connected") return "text-emerald-500";
  if (status === "error") return "text-red-500";
  return "text-[var(--text)]/60";
}
