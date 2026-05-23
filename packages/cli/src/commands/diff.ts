/**
 * `kiclaude diff <a.kicad_pcb> <b.kicad_pcb>` — M2-T-11 CLI subcommand.
 *
 * Shells out to `kc_mcp.diff_cli` for the structural delta. Default
 * output is JSON; `--pr` switches to a compact +/-/~ report suitable
 * for pasting into a PR description. `--svg <path>` requests a visual
 * diff via pcbdraw (best-effort — falls back gracefully if pcbdraw
 * isn't on PATH).
 *
 * Exit codes: 0 = no changes, 1 = changes found, 2 = bad args / parse
 * error, 127 = Python missing.
 */

import { spawn } from "node:child_process";

export interface RunDiffOptions {
  /** First `.kicad_pcb` path. */
  before: string;
  /** Second `.kicad_pcb` path. */
  after: string;
  /** Optional SVG output path (requires pcbdraw). */
  svg?: string;
  /** PR-friendly compact output. */
  pr?: boolean;
  /** Disable ANSI color in pr mode. */
  noColor?: boolean;
  /** Python interpreter. Defaults to `$KICLAUDE_PYTHON` or `python3`. */
  python?: string;
  /** Test seam — override the spawned module name. */
  module?: string;
  /** Test seam — override the spawn factory. */
  spawnImpl?: typeof spawn;
}

export async function runDiff(opts: RunDiffOptions): Promise<number> {
  const python = opts.python ?? process.env.KICLAUDE_PYTHON ?? "python3";
  const module = opts.module ?? "kc_mcp.diff_cli";
  const spawnFn = opts.spawnImpl ?? spawn;
  const args: string[] = ["-m", module, opts.before, opts.after];
  if (opts.svg) args.push("--svg", opts.svg);
  if (opts.pr) args.push("--pr");
  if (opts.noColor) args.push("--no-color");

  const child = spawnFn(python, args, {
    stdio: ["inherit", "inherit", "inherit"],
    env: process.env,
  });

  return await new Promise<number>((resolve) => {
    let resolved = false;
    const settle = (code: number): void => {
      if (resolved) return;
      resolved = true;
      resolve(code);
    };
    child.on("exit", (code, signal) => {
      if (typeof code === "number") {
        settle(code);
      } else if (signal) {
        settle(128);
      } else {
        settle(1);
      }
    });
    child.on("error", (err) => {
      process.stderr.write(
        `kiclaude diff: failed to spawn ${python} -m ${module}: ${err.message}\n`,
      );
      settle(127);
    });
  });
}
