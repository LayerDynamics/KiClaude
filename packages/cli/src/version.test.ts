import { describe, expect, it } from "vitest";

import { cliVersion } from "./version.js";

describe("cliVersion", () => {
  it("returns a non-empty semver-shaped string", () => {
    const v = cliVersion();
    expect(v).toMatch(/^\d+\.\d+\.\d+/);
  });
});
