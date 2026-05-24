import { describe, expect, it } from "vitest";

import { _parseHashForTests } from "./router";

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
});
