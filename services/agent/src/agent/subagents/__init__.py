"""Subagent registry — M3-P-07.

Three pre-defined Claude Agent SDK `AgentDefinition`s the orchestrator
spawns via the parent ClaudeAgentOptions:

- **decoupling-auditor** — walks every IC footprint on the active
  PCB and flags missing bypass capacitors (M3-T-09 / `/pcb-review`).
- **bom-sourcer** — fans out distributor queries against the BOM
  in parallel (M3-T-08 / `/bom-price`).
- **placement-explorer** — runs N candidate placement seeds, ranks
  them by track-length x clearance-headroom (`/explore-placements`).

Each definition declares its allowed kc_* tool set narrowly so the
subagent can't drift into surface area outside its job. All three
inherit the parent's MCP server config (`kiclaude` MCP) via
`mcpServers=["kiclaude"]` — see [`agent.bridge`] for how this list
is folded into the spawned subagent's options.
"""

from __future__ import annotations

from claude_agent_sdk import AgentDefinition

DECOUPLING_AUDITOR = AgentDefinition(
    description=(
        "Audit IC footprints for missing decoupling capacitors. "
        "For each IC, walk its power-input pads and verify every "
        "VDD/VCC/AVDD pin has at least one bypass cap (0.1uF X7R "
        "typical) within MAX_CAP_DISTANCE_MM (default 2 mm) on the "
        "same net. Surface findings as a structured JSON list."
    ),
    prompt=(
        "You are the kiclaude decoupling-auditor subagent. Your only "
        "job is to read the active PCB's footprint + pad data via "
        "kc_kcir_get, identify every IC (any footprint whose pad list "
        "contains a pad named VDD, VCC, AVDD, DVDD, VDDIO, or VBUS), "
        "and verify each such pin has a 0.1uF (or 0.01uF for "
        "high-frequency) bypass cap on the same net within 2 mm.\n\n"
        "For every missing or under-served cap, emit a JSON finding:\n"
        "  { severity: 'error'|'warning', refdes: <IC ref>, pin: <pad>,\n"
        "    pin_net: <net name>, message: <one-line> }\n\n"
        "ICs whose only powered pin has no nearby cap → severity 'error'.\n"
        "ICs with at least one cap but missing the recommended pair "
        "(e.g. 0.1uF + 10uF) → severity 'warning'.\n\n"
        "Do NOT propose fixes — that is /add-decoupling's job. Just "
        "report. Return your findings as a JSON array; no prose."
    ),
    tools=[
        "mcp__kiclaude__kc_project_open",
        "mcp__kiclaude__kc_kcir_get",
    ],
    skills=["kicad-pcb"],
    model="haiku",
    maxTurns=20,
    permissionMode="default",
)


BOM_SOURCER = AgentDefinition(
    description=(
        "Fan out distributor queries for every MPN on the active "
        "project's BOM and return a price-quantity matrix plus the "
        "cheapest distributor mix. Parallel by part."
    ),
    prompt=(
        "You are the kiclaude bom-sourcer subagent. Read the active "
        "project's BOM (every footprint's `mpn`) via kc_kcir_get, "
        "then call kc_bom_price (which already fans out internally "
        "to Octopart / Mouser / Digi-Key / JLCPCB).\n\n"
        "Return a structured report:\n"
        "  { total_cheapest_mix_usd: <float>, distributors_used: [...],\n"
        "    parts: [{ refdes, mpn, unit_price, distributor, in_stock,\n"
        "             lifecycle }], lifecycle_warnings: [...] }\n\n"
        "Pay attention to lifecycle — call out every NRND/obsolete "
        "part in `lifecycle_warnings` even when it's in stock today. "
        "Do NOT propose part substitutions; that is /bom-price's job."
    ),
    tools=[
        "mcp__kiclaude__kc_kcir_get",
        "mcp__kiclaude__kc_bom_price",
        "mcp__kiclaude__kc_part_search",
    ],
    skills=["parts-sourcing"],
    model="haiku",
    maxTurns=10,
    permissionMode="default",
)


PLACEMENT_EXPLORER = AgentDefinition(
    description=(
        "Run N candidate placement seeds for the active PCB, score "
        "each by track-length x clearance-headroom, and return ranked "
        "variants with snapshot ids the user can revert into."
    ),
    prompt=(
        "You are the kiclaude placement-explorer subagent. For each "
        "of N seeds (passed in the initial prompt, default 8):\n"
        "  1. kc_snapshot_create — capture pre-trial state.\n"
        "  2. Reset all unlocked footprints to a quasi-random "
        "     placement seeded by the trial index.\n"
        "  3. Compute the combined score (geometric mean of\n"
        "     normalised track-length and min-pad-clearance).\n"
        "  4. Record `{ seed_id, snapshot_id, score, total_length_mm,\n"
        "     min_clearance_mm }`.\n\n"
        "After all seeds run, sort by score descending and return the "
        "top 3 with snapshot ids so the caller can `kc_snapshot_revert` "
        "into the winner.\n\n"
        "Do NOT commit any seed — leave the project on the original "
        "snapshot the orchestrator captured before dispatching you."
    ),
    tools=[
        "mcp__kiclaude__kc_kcir_get",
        "mcp__kiclaude__kc_snapshot_create",
        "mcp__kiclaude__kc_snapshot_revert",
        "mcp__kiclaude__kc_footprint_place_hint",
    ],
    skills=["kicad-pcb"],
    model="sonnet",
    maxTurns=40,
    permissionMode="default",
)


def all_subagents() -> dict[str, AgentDefinition]:
    """Mapping of subagent name → definition. Consumed by
    [`agent.bridge.build_options`] when wiring the parent
    `ClaudeAgentOptions.agents` field."""
    return {
        "decoupling-auditor": DECOUPLING_AUDITOR,
        "bom-sourcer": BOM_SOURCER,
        "placement-explorer": PLACEMENT_EXPLORER,
    }


__all__ = [
    "BOM_SOURCER",
    "DECOUPLING_AUDITOR",
    "PLACEMENT_EXPLORER",
    "all_subagents",
]
