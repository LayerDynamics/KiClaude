/**
 * `chatStore` — message log + transport status for the kiclaude
 * chat sidebar. Persists to localStorage so closing/reopening the
 * sidebar (M0-T-03) preserves the conversation.
 */

import { create } from "zustand";
import { devtools, persist } from "zustand/middleware";

export type ChatRole = "user" | "assistant" | "system";

export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  /** UTC ISO-8601. */
  ts: string;
  /** `true` while the assistant is still streaming this message. */
  streaming?: boolean;
}

export type ChatStatus = "disconnected" | "connecting" | "connected" | "error";

interface ChatState {
  messages: ChatMessage[];
  status: ChatStatus;
  error: string | null;
  send: (content: string) => void;
  appendAssistantToken: (id: string, token: string) => void;
  finalizeAssistant: (id: string) => void;
  setStatus: (status: ChatStatus, error?: string | null) => void;
  clear: () => void;
}

export const useChatStore = create<ChatState>()(
  devtools(
    persist(
      (set) => ({
        messages: [],
        status: "disconnected",
        error: null,
        send(content) {
          set((state) => ({
            messages: [
              ...state.messages,
              {
                id: cryptoRandomId(),
                role: "user",
                content,
                ts: new Date().toISOString(),
              },
            ],
          }));
        },
        appendAssistantToken(id, token) {
          set((state) => {
            const existing = state.messages.find((m) => m.id === id);
            if (existing) {
              return {
                messages: state.messages.map((m) =>
                  m.id === id ? { ...m, content: m.content + token, streaming: true } : m,
                ),
              };
            }
            return {
              messages: [
                ...state.messages,
                {
                  id,
                  role: "assistant",
                  content: token,
                  ts: new Date().toISOString(),
                  streaming: true,
                },
              ],
            };
          });
        },
        finalizeAssistant(id) {
          set((state) => ({
            messages: state.messages.map((m) =>
              m.id === id ? { ...m, streaming: false } : m,
            ),
          }));
        },
        setStatus(status, error = null) {
          set(() => ({ status, error }));
        },
        clear() {
          set(() => ({ messages: [], status: "disconnected", error: null }));
        },
      }),
      { name: "kiclaude.chat" },
    ),
    { name: "chatStore" },
  ),
);

function cryptoRandomId(): string {
  if (typeof globalThis.crypto?.randomUUID === "function") {
    return globalThis.crypto.randomUUID();
  }
  // happy-dom / older runtimes: fall back to Math.random — fine for IDs.
  return `m-${Math.random().toString(36).slice(2, 10)}-${Date.now().toString(36)}`;
}
