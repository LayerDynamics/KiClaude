import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { KiclaudeWsClient, type KiclaudeWsListener } from "../../lib/ws";
import { useChatStore } from "../../stores/chatStore";
import { ChatSidebar } from "./ChatSidebar";

/**
 * A mock WS that lets the test fire events into the React listener.
 */
class MockWsClient extends KiclaudeWsClient {
  // Distinct field name from the parent's private `listeners` to avoid
  // a TS error about clashing private declarations.
  private mockListeners = new Set<KiclaudeWsListener>();
  sent: Array<string | Record<string, unknown>> = [];

  constructor() {
    super({
      heartbeatMs: null,
      socketFactory: () => ({}) as unknown as WebSocket,
    });
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

describe("ChatSidebar", () => {
  beforeEach(() => useChatStore.getState().clear());
  afterEach(() => {
    cleanup();
    useChatStore.getState().clear();
  });

  it("renders connected status after the WS opens", async () => {
    const client = new MockWsClient();
    render(<ChatSidebar client={client} />);
    await waitFor(() => expect(screen.getByTestId("chat-status").textContent).toBe("connected"));
  });

  it("streams assistant tokens incrementally", async () => {
    const client = new MockWsClient();
    render(<ChatSidebar client={client} />);
    await waitFor(() => screen.getByTestId("chat-status"));
    act(() => {
      client.fire({ kind: "json", data: { kind: "assistant_token", id: "m1", token: "Hel" } });
      client.fire({ kind: "json", data: { kind: "assistant_token", id: "m1", token: "lo" } });
    });
    const item = await waitFor(() => screen.getByTestId("chat-msg-assistant"));
    expect(item.textContent).toContain("Hello");
    expect(item.dataset.streaming).toBe("true");
    act(() => client.fire({ kind: "json", data: { kind: "assistant_end", id: "m1" } }));
    await waitFor(() =>
      expect(screen.getByTestId("chat-msg-assistant").dataset.streaming).toBe("false"),
    );
  });

  it("send button posts the prompt over the WS and to the store", async () => {
    const client = new MockWsClient();
    render(<ChatSidebar client={client} />);
    await waitFor(() => screen.getByTestId("chat-status"));
    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "hello kiclaude" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    await waitFor(() => expect(screen.getByTestId("chat-msg-user").textContent).toContain("hello kiclaude"));
    expect(client.sent.at(-1)).toEqual({ kind: "user_prompt", content: "hello kiclaude" });
  });

  it("closing then reopening preserves history (zustand persist)", async () => {
    const client = new MockWsClient();
    const { rerender } = render(<ChatSidebar client={client} initiallyOpen />);
    await waitFor(() => screen.getByTestId("chat-status"));
    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "remember me" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    await waitFor(() => screen.getByTestId("chat-msg-user"));
    fireEvent.click(screen.getByTestId("chat-sidebar-close"));
    // Reopen.
    fireEvent.click(screen.getByTestId("chat-sidebar-open"));
    rerender(<ChatSidebar client={client} initiallyOpen />);
    await waitFor(() =>
      expect(screen.getByTestId("chat-msg-user").textContent).toContain("remember me"),
    );
  });
});
