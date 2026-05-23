/**
 * Unit tests for the `kiclaude diff` TS shell.
 */

import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import { runDiff } from "./diff.js";

function fakeChild(exitCode: number | null, signal?: string) {
  const emitter = new EventEmitter() as EventEmitter & {
    on: typeof EventEmitter.prototype.on;
  };
  queueMicrotask(() => {
    emitter.emit("exit", exitCode, signal);
  });
  return emitter;
}

describe("runDiff", () => {
  it("spawns python with both PCB paths", async () => {
    const spawnImpl = vi.fn(() => fakeChild(1)) as unknown as typeof import("node:child_process").spawn;
    const exit = await runDiff({
      before: "a.kicad_pcb",
      after: "b.kicad_pcb",
      python: "python3",
      spawnImpl,
    });
    // Exit 1 = changes found, expected for two distinct paths.
    expect(exit).toBe(1);
    const call = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(call[0]).toBe("python3");
    expect(call[1]).toEqual(["-m", "kc_mcp.diff_cli", "a.kicad_pcb", "b.kicad_pcb"]);
  });

  it("forwards --svg, --pr, --no-color", async () => {
    const spawnImpl = vi.fn(() => fakeChild(0)) as unknown as typeof import("node:child_process").spawn;
    await runDiff({
      before: "a.kicad_pcb",
      after: "b.kicad_pcb",
      svg: "diff.svg",
      pr: true,
      noColor: true,
      spawnImpl,
    });
    const args = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]![1] as string[];
    expect(args).toEqual([
      "-m",
      "kc_mcp.diff_cli",
      "a.kicad_pcb",
      "b.kicad_pcb",
      "--svg",
      "diff.svg",
      "--pr",
      "--no-color",
    ]);
  });

  it("returns 127 when spawn errors", async () => {
    const spawnImpl = vi.fn(() => {
      const ee = new EventEmitter();
      queueMicrotask(() => ee.emit("error", new Error("ENOENT")));
      return ee as unknown as ReturnType<typeof import("node:child_process").spawn>;
    }) as unknown as typeof import("node:child_process").spawn;
    const exit = await runDiff({
      before: "a.kicad_pcb",
      after: "b.kicad_pcb",
      python: "nope",
      spawnImpl,
    });
    expect(exit).toBe(127);
  });
});
