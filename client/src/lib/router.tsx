/**
 * Minimal hash-based router — M3-T-07.
 *
 * We deliberately don't pull in `react-router-dom` for one extra
 * route. The hash (`location.hash === "#/three"`) is a complete
 * SPA-routing primitive that survives reloads without server-side
 * rewrites, doesn't fight with the dev server, and stays out of
 * the way of the existing single-page layout. If the route surface
 * grows beyond two or three pages, swap this for `react-router-dom`
 * without touching the call sites.
 */

import { useEffect, useState } from "react";

export type AppRoute = "/" | "/three" | "/share";

function parseHash(hash: string): AppRoute {
  // Strip the leading `#`. Treat `#/three` and `#/three/` as the
  // same destination; everything else falls back to root.
  const raw = hash.replace(/^#/, "").replace(/\/+$/, "") || "/";
  if (raw === "/three") return "/three";
  // `#/share/<token>` — the FR-080 read-only share link. `#/share`
  // with no token still routes here; the page surfaces the missing
  // token as an error rather than silently dropping to root.
  if (raw === "/share" || raw.startsWith("/share/")) return "/share";
  return "/";
}

/** Extract the share token from a `#/share/<token>` hash, or `null`. */
function parseShareToken(hash: string): string | null {
  const raw = hash.replace(/^#/, "").replace(/\/+$/, "");
  if (!raw.startsWith("/share/")) return null;
  const token = raw.slice("/share/".length);
  return token.length > 0 ? token : null;
}

export function useRoute(): AppRoute {
  const [route, setRoute] = useState<AppRoute>(() =>
    typeof globalThis.location !== "undefined" ? parseHash(globalThis.location.hash) : "/",
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onHashChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return route;
}

/** Reactive share token from the current `#/share/<token>` hash. */
export function useShareToken(): string | null {
  const [token, setToken] = useState<string | null>(() =>
    typeof globalThis.location !== "undefined"
      ? parseShareToken(globalThis.location.hash)
      : null,
  );
  useEffect(() => {
    if (typeof window === "undefined") return;
    const onHashChange = () => setToken(parseShareToken(window.location.hash));
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);
  return token;
}

/** Test-side helpers that export the parsers so unit tests can verify
 * the route mapping without a real DOM. */
export const _parseHashForTests = parseHash;
export const _parseShareTokenForTests = parseShareToken;
