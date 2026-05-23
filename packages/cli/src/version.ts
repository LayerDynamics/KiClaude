import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * Resolve the kiclaude CLI version from this package's `package.json`.
 *
 * Walks up from the compiled module's directory until it finds a
 * `package.json` with `name === "@kiclaude/cli"`. This is robust to
 * being installed under `dist/`, run from source via `tsx`, or
 * symlinked into a global bin directory.
 */
export function cliVersion(): string {
  const here = dirname(fileURLToPath(import.meta.url));
  // Walk up at most 5 levels — that's enough to escape `dist/`,
  // `node_modules/<...>/dist/`, or a global symlink chain.
  let current = here;
  for (let i = 0; i < 5; i += 1) {
    const candidate = join(current, "package.json");
    try {
      const raw = readFileSync(candidate, "utf8");
      const parsed = JSON.parse(raw) as { name?: string; version?: string };
      if (parsed.name === "@kiclaude/cli" && typeof parsed.version === "string") {
        return parsed.version;
      }
    } catch {
      // No package.json at this level — keep walking.
    }
    const parent = dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return "0.0.0";
}
