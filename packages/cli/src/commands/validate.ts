/**
 * `kiclaude validate <project>` — M1-T-09 CLI subcommand.
 *
 * The validation logic lives on the Python side
 * (`kc_mcp.validate_cli`) so the KC001..KC011 validators + kicad-cli
 * ERC bridge stay in one place. This module is a thin shell: spawn
 * the Python entry, stream stdout/stderr to the user, return the
 * child's exit code (0 = clean, 1 = errors found, 2 = invalid args).
 */

import { spawn } from "node:child_process";

export interface RunValidateOptions {
  /** Project directory or `.kicad_pro` file. */
  project: string;
  /** Pass `--json` through to the Python entry. */
  json?: boolean;
  /** Skip the kicad-cli ERC pass. */
  skipErc?: boolean;
  /** Suppress ANSI color in the human report. */
  noColor?: boolean;
  /** Python interpreter. Defaults to `$KICLAUDE_PYTHON` or `python3`. */
  python?: string;
  /** Test seam — override the spawned module name. */
  module?: string;
  /** Test seam — override the spawn factory. */
  spawnImpl?: typeof spawn;
}

export async function runValidate(opts: RunValidateOptions): Promise<number> {
  const python = opts.python ?? process.env.KICLAUDE_PYTHON ?? "python3";
  const module = opts.module ?? "kc_mcp.validate_cli";
  const spawnFn = opts.spawnImpl ?? spawn;
  const args: string[] = ["-m", module, opts.project];
  if (opts.json) args.push("--json");
  if (opts.skipErc) args.push("--skip-erc");
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
        `kiclaude validate: failed to spawn ${python} -m ${module}: ${err.message}\n`,
      );
      settle(127);
    });
  });
}
