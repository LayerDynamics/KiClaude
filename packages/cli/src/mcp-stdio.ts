import { spawn } from "node:child_process";

/**
 * Spawn the kiclaude Python MCP server in stdio mode and proxy
 * stdin/stdout between the parent process and the child.
 *
 * The Python entry point lives at `services/mcp/src/kc_mcp/stdio.py`
 * (started via `python -m kc_mcp.stdio`). The CLI command
 * `kiclaude mcp stdio` is the cross-language hand-off — Claude Code
 * (or any MCP client) invokes the CLI; the CLI shells out to Python.
 *
 * Returns the spawned child's exit code (or `1` if it was killed by
 * a signal). The function never returns until the child exits.
 */
export async function runMcpStdio(opts: McpStdioOptions = {}): Promise<number> {
  const python = opts.python ?? process.env.KICLAUDE_PYTHON ?? "python3";
  const module = opts.module ?? "kc_mcp.stdio";
  const child = spawn(python, ["-m", module], {
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
        `kiclaude mcp stdio: failed to spawn ${python} -m ${module}: ${err.message}\n`,
      );
      settle(127);
    });
  });
}

export interface McpStdioOptions {
  /** Python interpreter. Defaults to `python3` (or `$KICLAUDE_PYTHON`). */
  python?: string;
  /** Module to run with `python -m`. Defaults to `kc_mcp.stdio`. */
  module?: string;
}
