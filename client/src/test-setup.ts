// Vitest setup — auto-cleanup mounted React trees between tests so
// tests don't see leftover DOM from previous renders.
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
