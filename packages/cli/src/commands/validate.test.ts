/**
 * Unit tests for the `kiclaude validate` TS shell. The actual KCIR
 * validators live on the Python side; here we just verify the
 * shell forwards args correctly and surfaces the child's exit code.
 */

import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import { runValidate } from "./validate.js";

function fakeChild(exitCode: number | null, signal?: string) {
  const emitter = new EventEmitter() as EventEmitter & {
    on: typeof EventEmitter.prototype.on;
  };
  queueMicrotask(() => {
    emitter.emit("exit", exitCode, signal);
  });
  return emitter;
}

describe("runValidate", () => {
  it("spawns python with the right module + project arg", async () => {
    const spawnImpl = vi.fn(() => fakeChild(0)) as unknown as typeof import("node:child_process").spawn;
    const exit = await runValidate({
      project: "/tmp/blinky",
      python: "python3",
      spawnImpl,
    });
    expect(exit).toBe(0);
    expect(spawnImpl).toHaveBeenCalledTimes(1);
    const call = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(call[0]).toBe("python3");
    const args = call[1] as string[];
    expect(args).toEqual(["-m", "kc_mcp.validate_cli", "/tmp/blinky"]);
  });

  it("passes --json and --skip-erc through", async () => {
    const spawnImpl = vi.fn(() => fakeChild(1)) as unknown as typeof import("node:child_process").spawn;
    const exit = await runValidate({
      project: "/tmp/p",
      json: true,
      skipErc: true,
      noColor: true,
      spawnImpl,
    });
    expect(exit).toBe(1);
    const args = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]![1] as string[];
    expect(args).toEqual([
      "-m",
      "kc_mcp.validate_cli",
      "/tmp/p",
      "--json",
      "--skip-erc",
      "--no-color",
    ]);
  });

  it("uses $KICLAUDE_PYTHON when no python override is provided", async () => {
    const prev = process.env.KICLAUDE_PYTHON;
    process.env.KICLAUDE_PYTHON = "/opt/py/bin/python";
    try {
      const spawnImpl = vi.fn(() => fakeChild(0)) as unknown as typeof import("node:child_process").spawn;
      await runValidate({ project: ".", spawnImpl });
      const call = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
      expect(call[0]).toBe("/opt/py/bin/python");
    } finally {
      if (prev === undefined) delete process.env.KICLAUDE_PYTHON;
      else process.env.KICLAUDE_PYTHON = prev;
    }
  });

  it("returns 128 when the child is killed by a signal", async () => {
    const spawnImpl = vi.fn(() => fakeChild(null, "SIGTERM")) as unknown as typeof import("node:child_process").spawn;
    const exit = await runValidate({ project: ".", spawnImpl });
    expect(exit).toBe(128);
  });

  it("returns 127 when spawn errors out", async () => {
    const spawnImpl = vi.fn(() => {
      const ee = new EventEmitter();
      queueMicrotask(() => ee.emit("error", new Error("ENOENT")));
      return ee as unknown as ReturnType<typeof import("node:child_process").spawn>;
    }) as unknown as typeof import("node:child_process").spawn;
    const exit = await runValidate({
      project: ".",
      python: "missing-python",
      spawnImpl,
    });
    expect(exit).toBe(127);
  });
});
