---
name: parts-sourcing
description: Pick the right distributor for a given BOM lookup, interpret lifecycle status, propose alternates when a part is unavailable, and surface unit-price + assembly-cost tradeoffs. Use when the user asks "where can I buy this", "what's the BOM cost", or "find an alternate for X".
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_part_search
  - mcp__kiclaude__kc_part_alternates
  - mcp__kiclaude__kc_bom_price
---

# parts-sourcing — distributor decision tree

The four distributors kiclaude integrates with (M3-P-01..04) cover
overlapping but distinct slices of the parts universe. Picking the
right one cuts query time and avoids false "out of stock" reports
that are really "this distributor doesn't carry it".

## Distributor selection

| Goal                                  | Use         | Why                                                          |
| ------------------------------------- | ----------- | ------------------------------------------------------------ |
| Aggregate availability across all     | **Octopart**| Indexes ~30 distributors; single GraphQL query returns the   |
| reasonable distributors               |             | best-price-by-quantity matrix.                               |
| Western-hemisphere prototype quantity | **Mouser**  | No minimum order, fastest US ship times, broad active-part   |
| (1–100 units)                         |             | catalog.                                                     |
| Same as above, with cut-tape requested| **Digi-Key**| Honors cut-tape on most active parts; Mouser charges full    |
|                                       |             | reel premium for many MFRs.                                  |
| JLCPCB SMT assembly (LCSC parts only) | **JLCPCB**  | The only source whose catalog matches the assembler's hand-  |
|                                       |             | loaded feeders. "Basic" parts have no per-part setup fee.    |

**Rule of thumb**: query Octopart first for discovery, then hit the
distributor with the best price/availability for that part directly
to confirm stock.

## Lifecycle status

Every part returns a `lifecycle` field. Treat them as:

- `active` — safe to design in.
- `not_recommended_for_new_design` — design-stage red flag.
  Always offer an alternate via `kc_part_alternates` before
  committing the part to the BOM.
- `obsolete` / `last_time_buy` — fail-closed. The board may build
  this run but won't be re-buildable. Tell the user explicitly.

## Alternate-part selection

When the requested part is unavailable, `kc_part_alternates`
returns ranked candidates. Filter by:

1. **Footprint match** — the alternate must use the exact same
   KiCad footprint (`lib_id`). A "drop-in equivalent" with a
   different pad layout is not actually a drop-in.
2. **Electrical parameters within ±10%** — for a passive, that
   means tolerance + value + voltage rating. For an active, that
   means pinout + supply range + speed grade.
3. **Lifecycle ≥ requested part's lifecycle** — never propose an
   alternate that's more obsolete than what you're replacing.

If no candidate passes all three, surface that explicitly rather
than relaxing a criterion silently.

## BOM cost surfacing

`kc_bom_price` returns a per-line `{distributor, unit_price,
extended_price, in_stock, lifecycle}` table. When summarising for
the user:

- Show the **distributor mix** that minimises total cost at the
  requested quantity, not just per-line cheapest. Splitting a 20-
  line order across 4 distributors usually loses to single-source
  shipping + minimums.
- Always state the **assumed quantity**. A 10-unit BOM and a
  1000-unit BOM pick different distributors.
- For JLCPCB assembly quotes, separate "Basic" (no setup) from
  "Extended" ($3 setup per part). A board with 5 "Extended" parts
  is +$15 vs the same parts substituted for Basic equivalents.

## Failure modes

- **API rate limits**: the four distributor clients all enforce
  per-key rate limits. If a query 429s, the pricer's offline
  cache (M3-P-05) serves the last-known price with a `stale: true`
  flag. Surface that staleness to the user — a 7-day-old quote
  may have missed a 20% price move.
- **Missing parts**: if a part has no MPN in the BOM, none of the
  distributors can find it. Always run `kc_validate` first to
  catch MPN-missing rows before they reach the pricer.
