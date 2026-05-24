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

export type AppRoute = "/" | "/three";

function parseHash(hash: string): AppRoute {
  // Strip the leading `#`. Treat `#/three` and `#/three/` as the
  // same destination; everything else falls back to root.
  const raw = hash.replace(/^#/, "").replace(/\/+$/, "") || "/";
  return raw === "/three" ? "/three" : "/";
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

/** Test-side helper that exports the parser so unit tests can verify
 * the route mapping without a real DOM. */
export const _parseHashForTests = parseHash;
