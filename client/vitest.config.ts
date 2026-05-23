import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "happy-dom",
    include: ["src/**/*.test.{ts,tsx}"],
    globals: false,
    setupFiles: ["./src/test-setup.ts"],
  },
  // The wasm-pack `?init` magic is a Vite plugin feature and isn't
  // needed in unit tests (we mock the modules instead).
  optimizeDeps: {
    exclude: ["kiclaude-ki", "kiclaude-cad"],
  },
});
