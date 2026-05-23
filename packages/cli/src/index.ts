#!/usr/bin/env node
import { defineCommand, runMain } from "citty";

import { runBuild } from "./commands/build.js";
import { runDiff } from "./commands/diff.js";
import { runValidate } from "./commands/validate.js";
import { runMcpStdio } from "./mcp-stdio.js";
import { cliVersion } from "./version.js";

const mcpStdioCommand = defineCommand({
  meta: {
    name: "stdio",
    description:
      "Run the kiclaude MCP server over stdio — spawns the Python services/mcp implementation.",
  },
  args: {
    python: {
      type: "string",
      description: "Python interpreter to use. Defaults to $KICLAUDE_PYTHON or python3.",
    },
  },
  async run({ args }) {
    const exit = await runMcpStdio({ python: args.python });
    process.exit(exit);
  },
});

const mcpCommand = defineCommand({
  meta: {
    name: "mcp",
    description: "MCP transport subcommands.",
  },
  subCommands: {
    stdio: mcpStdioCommand,
  },
});

const validateCommand = defineCommand({
  meta: {
    name: "validate",
    description:
      "Run KC001..KC011 KCIR validators + kicad-cli ERC against a KiCad project. Exits non-zero on any error-severity finding.",
  },
  args: {
    project: {
      type: "positional",
      required: true,
      description: "Project directory or .kicad_pro file.",
    },
    json: {
      type: "boolean",
      description: "Emit structured JSON instead of the human report.",
    },
    "skip-erc": {
      type: "boolean",
      description: "Skip the kicad-cli ERC pass (KCIR validators only).",
    },
    "no-color": {
      type: "boolean",
      description: "Disable ANSI color in the human report.",
    },
    python: {
      type: "string",
      description:
        "Python interpreter to use. Defaults to $KICLAUDE_PYTHON or python3.",
    },
  },
  async run({ args }) {
    const pythonArg = args.python;
    const exit = await runValidate({
      project: args.project as string,
      json: Boolean(args.json),
      skipErc: Boolean(args["skip-erc"]),
      noColor: Boolean(args["no-color"]),
      ...(typeof pythonArg === "string" && pythonArg
        ? { python: pythonArg }
        : {}),
    });
    process.exit(exit);
  },
});

const buildCommand = defineCommand({
  meta: {
    name: "build",
    description:
      "Run the full fab pipeline (validate → DRC → gerber + drill + PnP + BOM) against a KiCad project. Non-zero exit on any gate failure.",
  },
  args: {
    project: {
      type: "positional",
      required: true,
      description: "Project directory or .kicad_pro file.",
    },
    out: {
      type: "string",
      description: "Output directory for fab artifacts (defaults to <project>/dist).",
    },
    json: {
      type: "boolean",
      description: "Emit structured JSON instead of the human report.",
    },
    "no-color": {
      type: "boolean",
      description: "Disable ANSI color in the human report.",
    },
    "skip-erc": {
      type: "boolean",
      description: "Skip the ERC pass in the validate stage.",
    },
    "skip-drc": {
      type: "boolean",
      description: "Skip the DRC stage.",
    },
    "skip-export": {
      type: "boolean",
      description: "Skip the fab-export stages.",
    },
    python: {
      type: "string",
      description: "Python interpreter to use. Defaults to $KICLAUDE_PYTHON or python3.",
    },
  },
  async run({ args }) {
    const pythonArg = args.python;
    const outArg = args.out;
    const exit = await runBuild({
      project: args.project as string,
      ...(typeof outArg === "string" && outArg ? { outputDir: outArg } : {}),
      json: Boolean(args.json),
      noColor: Boolean(args["no-color"]),
      skipErc: Boolean(args["skip-erc"]),
      skipDrc: Boolean(args["skip-drc"]),
      skipExport: Boolean(args["skip-export"]),
      ...(typeof pythonArg === "string" && pythonArg ? { python: pythonArg } : {}),
    });
    process.exit(exit);
  },
});

const diffCommand = defineCommand({
  meta: {
    name: "diff",
    description:
      "Structural diff between two .kicad_pcb files. Exits 0 = no changes, 1 = changes found.",
  },
  args: {
    before: {
      type: "positional",
      required: true,
      description: "First .kicad_pcb path.",
    },
    after: {
      type: "positional",
      required: true,
      description: "Second .kicad_pcb path.",
    },
    svg: {
      type: "string",
      description: "Optional SVG output path (requires pcbdraw on PATH).",
    },
    pr: {
      type: "boolean",
      description: "PR-friendly compact +/-/~ output.",
    },
    "no-color": {
      type: "boolean",
      description: "Disable ANSI color in pr mode.",
    },
    python: {
      type: "string",
      description: "Python interpreter to use. Defaults to $KICLAUDE_PYTHON or python3.",
    },
  },
  async run({ args }) {
    const pythonArg = args.python;
    const svgArg = args.svg;
    const exit = await runDiff({
      before: args.before as string,
      after: args.after as string,
      ...(typeof svgArg === "string" && svgArg ? { svg: svgArg } : {}),
      pr: Boolean(args.pr),
      noColor: Boolean(args["no-color"]),
      ...(typeof pythonArg === "string" && pythonArg ? { python: pythonArg } : {}),
    });
    process.exit(exit);
  },
});

const main = defineCommand({
  meta: {
    name: "kiclaude",
    version: cliVersion(),
    description: "kiclaude — browser-native AI-native KiCad-compatible EDA.",
  },
  subCommands: {
    mcp: mcpCommand,
    validate: validateCommand,
    build: buildCommand,
    diff: diffCommand,
  },
});

runMain(main);
