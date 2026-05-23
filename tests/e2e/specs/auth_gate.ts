/**
 * Shared auth-gate helper for the M0/M1/M2 Playwright specs.
 *
 * Replaces the per-spec `!!process.env.ANTHROPIC_API_KEY` check —
 * the agent service now accepts any of `ANTHROPIC_API_KEY`,
 * `ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN`,
 * `CLAUDE_CODE_USE_BEDROCK=1`, `CLAUDE_CODE_USE_VERTEX=1`, or a
 * keychain credential from `claude login`. Hardcoding one env var
 * caused tests to skip on machines that were perfectly capable of
 * authenticating via the CLI's stored OAuth token.
 *
 * This helper does two things:
 *   1. **fast-env-probe** — if any of the accepted env vars are
 *      set, succeed immediately without contacting the agent
 *      service. This keeps the test setup path free of network
 *      I/O when the env clearly says "yes".
 *   2. **service-probe fallback** — if no env vars are set AND
 *      `E2E_FULL_STACK=1`, hit `GET /api/agent/auth/status` on the
 *      gateway. The agent's auth module runs the keychain probe
 *      and returns `{ok, source, detail}` — the test uses that as
 *      the source of truth.
 *
 * Returns `{ok, reason}`. `reason` is a one-liner suitable for
 * `test.skip(true, reason)`.
 */

const ENV_VARS_ACCEPTED = [
  "ANTHROPIC_API_KEY",
  "ANTHROPIC_AUTH_TOKEN",
  "CLAUDE_CODE_OAUTH_TOKEN",
] as const;

function envOptInPresent(): { ok: boolean; source: string } {
  for (const name of ENV_VARS_ACCEPTED) {
    if (process.env[name]) {
      return { ok: true, source: `env:${name}` };
    }
  }
  if (process.env.CLAUDE_CODE_USE_BEDROCK === "1") {
    return { ok: true, source: "env:CLAUDE_CODE_USE_BEDROCK" };
  }
  if (process.env.CLAUDE_CODE_USE_VERTEX === "1") {
    return { ok: true, source: "env:CLAUDE_CODE_USE_VERTEX" };
  }
  return { ok: false, source: "none" };
}

export interface AuthGateResult {
  ok: boolean;
  source: string;
  reason: string;
}

/** Best-effort auth probe. Network calls only when `fullStack` is
 *  set AND no env credential is present (keeps cold-start cheap). */
export async function probeAuth(
  options: { fullStack: boolean; baseUrl?: string } = { fullStack: false },
): Promise<AuthGateResult> {
  const fromEnv = envOptInPresent();
  if (fromEnv.ok) {
    return {
      ok: true,
      source: fromEnv.source,
      reason: `auth available via ${fromEnv.source}`,
    };
  }
  if (!options.fullStack) {
    return {
      ok: false,
      source: "none",
      reason:
        "no env credential and E2E_FULL_STACK!=1 — start the agent service " +
        "to probe the keychain via /api/agent/auth/status",
    };
  }
  const base = options.baseUrl ?? "http://localhost:8080";
  try {
    const resp = await fetch(`${base}/api/agent/auth/status`, {
      signal: AbortSignal.timeout(20_000),
    });
    if (!resp.ok) {
      return {
        ok: false,
        source: "agent-error",
        reason: `agent /auth/status returned ${resp.status} ${resp.statusText}`,
      };
    }
    const body = (await resp.json()) as {
      ok?: boolean;
      source?: string;
      detail?: string;
    };
    if (body.ok && body.source) {
      return {
        ok: true,
        source: body.source,
        reason: `auth available via ${body.source}: ${body.detail ?? ""}`,
      };
    }
    return {
      ok: false,
      source: body.source ?? "unknown",
      reason: body.detail ?? "agent reported no auth available",
    };
  } catch (err) {
    return {
      ok: false,
      source: "agent-unreachable",
      reason: `could not reach /api/agent/auth/status: ${
        err instanceof Error ? err.message : String(err)
      }`,
    };
  }
}

/** Synchronous best-effort — env-only. Use this when you can't
 *  await (e.g. test discovery). The async {@link probeAuth} is
 *  preferred because it catches keychain-only setups. */
export function probeAuthSync(): AuthGateResult {
  const env = envOptInPresent();
  if (env.ok) {
    return {
      ok: true,
      source: env.source,
      reason: `auth available via ${env.source}`,
    };
  }
  return {
    ok: false,
    source: "none",
    reason:
      "no env credential set (set ANTHROPIC_API_KEY, " +
      "ANTHROPIC_AUTH_TOKEN, CLAUDE_CODE_OAUTH_TOKEN, " +
      "CLAUDE_CODE_USE_BEDROCK=1, CLAUDE_CODE_USE_VERTEX=1, or run " +
      "`claude login` and start the agent service so /api/agent/auth/status " +
      "can probe the keychain)",
  };
}
