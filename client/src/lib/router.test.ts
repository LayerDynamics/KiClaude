import { describe, expect, it } from "vitest";

import { _parseHashForTests, _parseShareTokenForTests } from "./router";

describe("hash router (M3-T-07)", () => {
  it("treats no hash as root", () => {
    expect(_parseHashForTests("")).toBe("/");
  });

  it("maps `#/three` to /three", () => {
    expect(_parseHashForTests("#/three")).toBe("/three");
  });

  it("tolerates a trailing slash", () => {
    expect(_parseHashForTests("#/three/")).toBe("/three");
  });

  it("falls back to root for unknown destinations", () => {
    expect(_parseHashForTests("#/no-such-route")).toBe("/");
  });

  it("handles a leading slash without the `#`", () => {
    // Hash strings always start with `#`; this just guards against
    // accidental call-sites that strip it.
    expect(_parseHashForTests("/three")).toBe("/three");
  });

  it("maps `#/share/<token>` to /share (FR-080)", () => {
    expect(_parseHashForTests("#/share/abc123")).toBe("/share");
    expect(_parseHashForTests("#/share")).toBe("/share");
    expect(_parseHashForTests("#/share/abc123/")).toBe("/share");
  });

  it("extracts the share token from the hash", () => {
    expect(_parseShareTokenForTests("#/share/" + "a".repeat(64))).toBe("a".repeat(64));
    expect(_parseShareTokenForTests("#/share/abc/")).toBe("abc");
    // No token (bare `#/share`) → null so the page can flag it.
    expect(_parseShareTokenForTests("#/share")).toBeNull();
    expect(_parseShareTokenForTests("#/three")).toBeNull();
  });
});
