/**
 * `kiclaude build <project>` — M2-T-10 CLI subcommand.
 *
 * The pipeline (validate → DRC → gerber + drill + PnP + BOM) lives on
 * the Python side (`kc_mcp.build_cli`) so the kicad-cli subprocess
 * wrappers stay in one place. This module is a thin shell: spawn the
 * Python entry, stream stdout/stderr to the user, return the child's
 * exit code (0 = clean, 1 = any gate failed, 2 = invalid args, 127 =
 * Python missing).
 */

import { spawn } from "node:child_process";

export interface RunBuildOptions {
  /** Project directory or `.kicad_pro` file. */
  project: string;
  /** Output directory for fab artifacts. */
  outputDir?: string;
  /** Emit JSON instead of the human report. */
  json?: boolean;
  /** Suppress ANSI color in the human report. */
  noColor?: boolean;
  /** Skip the ERC pass in the validate stage. */
  skipErc?: boolean;
  /** Skip the DRC stage. */
  skipDrc?: boolean;
  /** Skip the fab-export stages. */
  skipExport?: boolean;
  /** Python interpreter. Defaults to `$KICLAUDE_PYTHON` or `python3`. */
  python?: string;
  /** Test seam — override the spawned module name. */
  module?: string;
  /** Test seam — override the spawn factory. */
  spawnImpl?: typeof spawn;
}

export async function runBuild(opts: RunBuildOptions): Promise<number> {
  const python = opts.python ?? process.env.KICLAUDE_PYTHON ?? "python3";
  const module = opts.module ?? "kc_mcp.build_cli";
  const spawnFn = opts.spawnImpl ?? spawn;
  const args: string[] = ["-m", module, opts.project];
  if (opts.outputDir) args.push("--out", opts.outputDir);
  if (opts.json) args.push("--json");
  if (opts.noColor) args.push("--no-color");
  if (opts.skipErc) args.push("--skip-erc");
  if (opts.skipDrc) args.push("--skip-drc");
  if (opts.skipExport) args.push("--skip-export");

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
        `kiclaude build: failed to spawn ${python} -m ${module}: ${err.message}\n`,
      );
      settle(127);
    });
  });
}
