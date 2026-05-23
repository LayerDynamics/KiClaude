/*
 * Build the vendored kicanvas.js from the pinned kicanvas source tree
 * checked into node_modules/kicanvas. Replicates kicanvas's own
 * scripts/bundle.js esbuild invocation so we get the same loaders and
 * the same `DEBUG=false` define without having to install kicanvas's
 * full devDependencies.
 *
 * Output: client/public/vendor/kicanvas.js (served from /vendor/kicanvas.js).
 *
 * Invoked automatically on `pnpm install` via the "postinstall" script
 * in client/package.json. Also runnable manually with
 *   pnpm -F client build:kicanvas
 */

import esbuild from "esbuild";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const clientRoot = resolve(__dirname, "..");
const kicanvasRoot = resolve(clientRoot, "node_modules/kicanvas");
const entry = resolve(kicanvasRoot, "src/index.ts");
const outdir = resolve(clientRoot, "public/vendor");
const outfile = resolve(outdir, "kicanvas.js");

if (!existsSync(entry)) {
  console.error(
    `[build-kicanvas] kicanvas source not found at ${entry}.\n` +
      `  This usually means \`pnpm install\` hasn't fetched the pinned\n` +
      `  GitHub dep yet. Run \`pnpm install\` first.`,
  );
  process.exit(1);
}

if (!existsSync(outdir)) {
  mkdirSync(outdir, { recursive: true });
}

const cssMinifyPlugin = {
  name: "kicanvas-css-minify",
  setup(build) {
    build.onLoad({ filter: /\.css$/ }, async (args) => {
      const f = readFileSync(args.path);
      const css = await esbuild.transform(f, {
        loader: "css",
        minify: true,
      });
      return { loader: "text", contents: css.code };
    });
  },
};

console.log(`[build-kicanvas] bundling ${entry}`);
console.log(`[build-kicanvas] output    ${outfile}`);

const startMs = Date.now();
// kicanvas is a Lit app and uses TypeScript experimental decorators
// (`@customElement`, `@property`). esbuild does not auto-load
// tsconfig.json files under `node_modules`, so we pass the relevant
// compiler options inline — without these the bundle errors at
// runtime with `TypeError: Object.defineProperty called on non-object`.
const tsconfigRaw = {
  compilerOptions: {
    target: "es2022",
    experimentalDecorators: true,
    useDefineForClassFields: false,
    verbatimModuleSyntax: true,
  },
};

const result = await esbuild.build({
  entryPoints: [entry],
  outfile,
  bundle: true,
  format: "esm",
  target: "es2022",
  keepNames: true,
  sourcemap: false,
  minify: true,
  loader: {
    ".js": "ts",
    ".glsl": "text",
    ".css": "text",
    ".svg": "text",
    ".kicad_wks": "text",
  },
  define: {
    DEBUG: "false",
  },
  tsconfigRaw,
  plugins: [cssMinifyPlugin],
  logLevel: "info",
});

const elapsed = Date.now() - startMs;
const warnings = result.warnings ?? [];
const errors = result.errors ?? [];
console.log(
  `[build-kicanvas] done in ${elapsed}ms — ${warnings.length} warnings, ${errors.length} errors`,
);

if (errors.length > 0) {
  for (const e of errors) console.error("  -", e.text);
  process.exit(1);
}
