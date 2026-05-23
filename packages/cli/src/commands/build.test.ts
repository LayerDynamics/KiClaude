/**
 * Unit tests for the `kiclaude build` TS shell. The pipeline lives on
 * the Python side; we just verify the shell forwards args correctly
 * and surfaces the child's exit code.
 */

import { EventEmitter } from "node:events";
import { describe, expect, it, vi } from "vitest";

import { runBuild } from "./build.js";

function fakeChild(exitCode: number | null, signal?: string) {
  const emitter = new EventEmitter() as EventEmitter & {
    on: typeof EventEmitter.prototype.on;
  };
  queueMicrotask(() => {
    emitter.emit("exit", exitCode, signal);
  });
  return emitter;
}

describe("runBuild", () => {
  it("spawns python with the right module + project arg", async () => {
    const spawnImpl = vi.fn(() => fakeChild(0)) as unknown as typeof import("node:child_process").spawn;
    const exit = await runBuild({
      project: "/tmp/blinky",
      python: "python3",
      spawnImpl,
    });
    expect(exit).toBe(0);
    const call = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]!;
    expect(call[0]).toBe("python3");
    expect(call[1]).toEqual(["-m", "kc_mcp.build_cli", "/tmp/blinky"]);
  });

  it("forwards --out, --json, --skip-erc/drc/export", async () => {
    const spawnImpl = vi.fn(() => fakeChild(1)) as unknown as typeof import("node:child_process").spawn;
    const exit = await runBuild({
      project: "/tmp/p",
      outputDir: "/tmp/out",
      json: true,
      noColor: true,
      skipErc: true,
      skipDrc: true,
      skipExport: true,
      spawnImpl,
    });
    expect(exit).toBe(1);
    const args = (spawnImpl as unknown as ReturnType<typeof vi.fn>).mock.calls[0]![1] as string[];
    expect(args).toEqual([
      "-m",
      "kc_mcp.build_cli",
      "/tmp/p",
      "--out",
      "/tmp/out",
      "--json",
      "--no-color",
      "--skip-erc",
      "--skip-drc",
      "--skip-export",
    ]);
  });

  it("returns 127 when spawn errors", async () => {
    const spawnImpl = vi.fn(() => {
      const ee = new EventEmitter();
      queueMicrotask(() => ee.emit("error", new Error("ENOENT")));
      return ee as unknown as ReturnType<typeof import("node:child_process").spawn>;
    }) as unknown as typeof import("node:child_process").spawn;
    const exit = await runBuild({ project: ".", python: "missing-python", spawnImpl });
    expect(exit).toBe(127);
  });

  it("returns 128 when the child is killed by a signal", async () => {
    const spawnImpl = vi.fn(() => fakeChild(null, "SIGTERM")) as unknown as typeof import("node:child_process").spawn;
    const exit = await runBuild({ project: ".", spawnImpl });
    expect(exit).toBe(128);
  });
});
