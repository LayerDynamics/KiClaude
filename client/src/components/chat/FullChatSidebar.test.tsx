import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { KiclaudeWsClient, type KiclaudeWsListener } from "../../lib/ws";
import { useChatStore } from "../../stores/chatStore";
import { ChatSidebar } from "./ChatSidebar";

class MockWsClient extends KiclaudeWsClient {
  private mockListeners = new Set<KiclaudeWsListener>();
  sent: Array<string | Record<string, unknown>> = [];

  constructor() {
    super({ heartbeatMs: null, socketFactory: () => ({}) as unknown as WebSocket });
  }
  override subscribe(listener: KiclaudeWsListener): () => void {
    this.mockListeners.add(listener);
    return () => this.mockListeners.delete(listener);
  }
  override connect(): void {
    this.fire({ kind: "open" });
  }
  override send(payload: string | Record<string, unknown>): boolean {
    this.sent.push(payload);
    return true;
  }
  override close(): void {
    this.fire({ kind: "close", code: 1000 });
  }
  fire(event: Parameters<KiclaudeWsListener>[0]): void {
    for (const l of this.mockListeners) l(event);
  }
}

describe("ChatSidebar M1-T-07 surfaces", () => {
  beforeEach(() => useChatStore.getState().clear());
  afterEach(() => {
    cleanup();
    useChatStore.getState().clear();
  });

  it("renders a tool-call card when tool_use_start fires and closes it on tool_use_end", async () => {
    const client = new MockWsClient();
    render(<ChatSidebar client={client} />);
    await waitFor(() => screen.getByTestId("chat-status"));
    act(() => {
      client.fire({
        kind: "json",
        data: {
          kind: "tool_use_start",
          id: "tc-1",
          tool_name: "kc_symbol_add",
          input: { lib_id: "Device:R" },
        },
      });
    });
    const card = await waitFor(() => screen.getByTestId("tool-call-card-tc-1"));
    expect(card.dataset.status).toBe("running");
    act(() => {
      client.fire({
        kind: "json",
        data: {
          kind: "tool_use_end",
          id: "tc-1",
          ok: true,
          duration_ms: 42,
          output: { ok: true, symbol_uuid: "u-1" },
        },
      });
    });
    await waitFor(() =>
      expect(screen.getByTestId("tool-call-card-tc-1").dataset.status).toBe("ok"),
    );
  });

  it("renders an AskUserQuestion card and sends the answer back over the WS", async () => {
    const client = new MockWsClient();
    render(<ChatSidebar client={client} />);
    await waitFor(() => screen.getByTestId("chat-status"));
    act(() => {
      client.fire({
        kind: "json",
        data: {
          kind: "ask_user_question",
          id: "q-1",
          question: "Footprint?",
          options: [
            { label: "0603" },
            { label: "0805" },
          ],
          multiSelect: false,
        },
      });
    });
    const card = await waitFor(() => screen.getByTestId("ask-user-question-q-1"));
    expect(card.dataset.answered).toBe("false");
    fireEvent.click(screen.getByTestId("ask-user-option-1"));
    fireEvent.click(screen.getByTestId("ask-user-submit"));
    expect(client.sent.at(-1)).toMatchObject({
      kind: "ask_user_question_answer",
      id: "q-1",
      picks: ["0805"],
    });
    expect(card.dataset.answered).toBe("true");
  });

  it("renders streaming-token messages with the blinking cursor and finalizes on end", async () => {
    const client = new MockWsClient();
    render(<ChatSidebar client={client} />);
    await waitFor(() => screen.getByTestId("chat-status"));
    act(() => {
      client.fire({ kind: "json", data: { kind: "assistant_token", id: "m1", token: "He" } });
      client.fire({ kind: "json", data: { kind: "assistant_token", id: "m1", token: "llo" } });
    });
    const msg = await waitFor(() => screen.getByTestId("chat-msg-assistant"));
    expect(msg.dataset.streaming).toBe("true");
    expect(msg.textContent).toContain("Hello");
    expect(msg.querySelector("[data-testid='streaming-cursor']")).toBeTruthy();
    act(() => client.fire({ kind: "json", data: { kind: "assistant_end", id: "m1" } }));
    await waitFor(() =>
      expect(screen.getByTestId("chat-msg-assistant").dataset.streaming).toBe("false"),
    );
  });
});
