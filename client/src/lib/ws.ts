/**
 * Browser WebSocket client with auto-reconnect + ping-heartbeat.
 *
 * Wraps the native `WebSocket` global so React components can subscribe
 * to typed events without dealing with the raw lifecycle. The default
 * URL targets the local gateway at `:8080/ws` (M0-T-01); override via
 * the `KICLAUDE_WS_URL` env var or the explicit constructor arg.
 */

export interface KiclaudeWsClientOptions {
  url?: string;
  /** Heartbeat interval in milliseconds. `null` disables heartbeats. */
  heartbeatMs?: number | null;
  /** Reconnect backoff base in ms; doubles each retry up to 30 s. */
  reconnectBaseMs?: number;
  /** Override the WebSocket constructor — test-only. */
  socketFactory?: (url: string) => WebSocket;
}

export type KiclaudeWsEvent =
  | { kind: "open" }
  | { kind: "close"; code: number }
  | { kind: "error"; error: string }
  | { kind: "message"; data: string }
  | { kind: "json"; data: unknown };

export type KiclaudeWsListener = (event: KiclaudeWsEvent) => void;

export class KiclaudeWsClient {
  private readonly url: string;
  private readonly heartbeatMs: number | null;
  private readonly reconnectBaseMs: number;
  private readonly socketFactory: (url: string) => WebSocket;
  private socket: WebSocket | null = null;
  private listeners = new Set<KiclaudeWsListener>();
  private retries = 0;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private closedByUser = false;

  constructor(opts: KiclaudeWsClientOptions = {}) {
    this.url = opts.url ?? defaultUrl();
    this.heartbeatMs = opts.heartbeatMs === undefined ? 25_000 : opts.heartbeatMs;
    this.reconnectBaseMs = opts.reconnectBaseMs ?? 500;
    this.socketFactory =
      opts.socketFactory ?? ((url) => new WebSocket(url));
  }

  /** Subscribe; returns an unsubscribe function. */
  subscribe(listener: KiclaudeWsListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Open the socket (or reuse the existing one). */
  connect(): void {
    if (this.socket && this.socket.readyState !== WebSocket.CLOSED) return;
    this.closedByUser = false;
    try {
      const ws = this.socketFactory(this.url);
      this.socket = ws;
      ws.addEventListener("open", () => this.handleOpen());
      ws.addEventListener("close", (e) => this.handleClose(e.code));
      ws.addEventListener("error", () => this.emit({ kind: "error", error: "ws error" }));
      ws.addEventListener("message", (e) => this.handleMessage(e.data));
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.emit({ kind: "error", error: msg });
      this.scheduleReconnect();
    }
  }

  /** Send a text frame. Buffers nothing — drops if not open. */
  send(payload: string | Record<string, unknown>): boolean {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return false;
    const text = typeof payload === "string" ? payload : JSON.stringify(payload);
    this.socket.send(text);
    return true;
  }

  /** Close gracefully; does not auto-reconnect after a user-initiated close. */
  close(code = 1000): void {
    this.closedByUser = true;
    this.clearTimers();
    this.socket?.close(code);
    this.socket = null;
  }

  private emit(event: KiclaudeWsEvent): void {
    for (const listener of this.listeners) {
      try {
        listener(event);
      } catch {
        // Swallow listener errors — one broken subscriber must not
        // wedge the others.
      }
    }
  }

  private handleOpen(): void {
    this.retries = 0;
    this.startHeartbeat();
    this.emit({ kind: "open" });
  }

  private handleClose(code: number): void {
    this.clearTimers();
    this.emit({ kind: "close", code });
    if (!this.closedByUser) {
      this.scheduleReconnect();
    }
  }

  private handleMessage(data: unknown): void {
    if (typeof data !== "string") return; // ignore binary for now
    this.emit({ kind: "message", data });
    try {
      const parsed: unknown = JSON.parse(data);
      this.emit({ kind: "json", data: parsed });
    } catch {
      // Non-JSON frame — `message` event already fired.
    }
  }

  private startHeartbeat(): void {
    if (!this.heartbeatMs || this.heartbeatTimer) return;
    this.heartbeatTimer = setInterval(() => {
      this.send({ kind: "ping", ts: Date.now() });
    }, this.heartbeatMs);
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;
    this.retries += 1;
    const backoff = Math.min(this.reconnectBaseMs * 2 ** (this.retries - 1), 30_000);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, backoff);
  }

  private clearTimers(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }
}

function defaultUrl(): string {
  // Vite's `import.meta.env.VITE_*` is the preferred surface but
  // works only at build time. For dev, prefer a plain `window.location`
  // derivation so the dev server's WS port matches automatically.
  if (typeof window !== "undefined") {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host || "127.0.0.1:8080";
    return `${proto}//${host.split(":")[0]}:8080/ws`;
  }
  return "ws://127.0.0.1:8080/ws";
}
