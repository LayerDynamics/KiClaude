import { useState } from "react";

import { Card, Input, Text } from "../UI";

export interface AskUserQuestionOption {
  label: string;
  description?: string;
}

export interface AskUserQuestion {
  /** Unique question id from the SDK so the response correlates. */
  id: string;
  /** The full question text (ends in `?`). */
  question: string;
  /** Optional 12-char header chip the SDK supplies. */
  header?: string;
  /** Two-to-four choices the user picks from. */
  options: AskUserQuestionOption[];
  /** When true, the user can pick more than one option. */
  multiSelect?: boolean;
}

export interface AskUserQuestionCardProps {
  question: AskUserQuestion;
  /** True once the user has answered — the card becomes read-only. */
  answered?: boolean;
  /** Pre-selected indices when re-rendering after answer. */
  preselected?: number[];
  /** Optional free-text supplied via "Other". */
  preselectedNotes?: string;
  /** Fired with the picked option labels + optional notes when the
   *  user clicks Submit. */
  onAnswer: (args: { picks: string[]; notes: string }) => void;
}

/**
 * M1-T-07 sub-component: multiple-choice card that surfaces the
 * Claude Agent SDK's `AskUserQuestion`. Single-select renders as
 * radios; multi-select renders as checkboxes. An "Other" row always
 * appears so the user can pass free text.
 */
export function AskUserQuestionCard(props: AskUserQuestionCardProps) {
  const { question, answered = false, preselected, preselectedNotes, onAnswer } = props;
  const [picks, setPicks] = useState<Set<number>>(
    () => new Set(preselected ?? []),
  );
  const [notes, setNotes] = useState(preselectedNotes ?? "");

  const single = !question.multiSelect;
  function toggle(idx: number): void {
    if (answered) return;
    setPicks((prev) => {
      const next = single ? new Set<number>() : new Set(prev);
      if (next.has(idx)) {
        next.delete(idx);
      } else {
        next.add(idx);
      }
      return next;
    });
  }

  function submit(): void {
    if (answered) return;
    const pickedLabels = Array.from(picks)
      .sort((a, b) => a - b)
      .map((i) => question.options[i]?.label ?? "")
      .filter((l) => l.length > 0);
    onAnswer({
      picks: pickedLabels,
      notes: notes.trim(),
    });
  }

  return (
    <Card
      tone="accent"
      flush
      data-testid={`ask-user-question-${question.id}`}
      data-answered={answered ? "true" : "false"}
      className={`my-1 ${answered ? "opacity-80" : ""}`}
    >
      <div className="p-2.5">
        {question.header ? (
          <span
            data-testid="ask-user-header"
            className="mb-1.5 inline-block rounded bg-[var(--accent)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-white"
          >
            {question.header}
          </span>
        ) : null}
        <Text variant="h4" className="mb-1.5 text-sm">
          {question.question}
        </Text>
        <ul className="m-0 list-none p-0">
          {question.options.map((opt, i) => {
            const checked = picks.has(i);
            return (
              <li key={`${opt.label}-${i}`} className="mb-1">
                <label
                  data-testid={`ask-user-option-${i}`}
                  className={`block cursor-pointer rounded p-1 text-sm ${
                    checked ? "bg-[var(--accent-bg)]" : ""
                  } ${answered ? "cursor-default" : "hover:bg-[var(--accent-bg)]/50"}`}
                >
                  <input
                    type={single ? "radio" : "checkbox"}
                    name={`ask-user-${question.id}`}
                    checked={checked}
                    onChange={() => toggle(i)}
                    disabled={answered}
                    className="mr-2 align-middle accent-[var(--accent)]"
                  />
                  <span className="font-semibold text-[var(--text-h)]">
                    {opt.label}
                  </span>
                  {opt.description ? (
                    <div className="ml-6 text-[11px] text-[var(--text)]/80">
                      {opt.description}
                    </div>
                  ) : null}
                </label>
              </li>
            );
          })}
        </ul>
        <div className="mt-1.5">
          <Input
            inputSize="sm"
            label="Other / notes"
            data-testid="ask-user-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={answered}
            placeholder="Optional free text"
          />
        </div>
        <button
          type="button"
          data-testid="ask-user-submit"
          onClick={submit}
          disabled={answered || (picks.size === 0 && notes.trim().length === 0)}
          className={`mt-2 inline-flex h-7 items-center rounded px-3 text-xs font-semibold text-white ${
            answered
              ? "cursor-default bg-[var(--text)]/40"
              : "cursor-pointer bg-[var(--accent)] hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          }`}
        >
          {answered ? "Answered" : "Submit"}
        </button>
      </div>
    </Card>
  );
}
