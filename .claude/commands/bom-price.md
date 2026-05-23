---
name: bom-price
description: Run the BOM pricer fan-out across Octopart / Mouser / Digi-Key / JLCPCB, aggregate results, and report a price-quantity matrix plus the cheapest distributor mix for the requested build quantity.
argument-hint: "[--quantity N] [--region us|eu|jp] [--prefer jlcpcb-assembly]   default: quantity=10 region=us"
allowed-tools:
  - mcp__kiclaude__kc_kcir_get
  - mcp__kiclaude__kc_validate
  - mcp__kiclaude__kc_part_search
  - mcp__kiclaude__kc_part_alternates
  - mcp__kiclaude__kc_bom_price
---

# /bom-price — sourcing report for the active BOM

Flow: read the BOM from the active project (every footprint's `mpn`),
fan out one parallel query per part to the M3-P-05 pricer, aggregate,
report.

## Steps

1. **Validate the BOM** — `kc_validate` confirms every footprint has
   a non-empty MPN. Missing-MPN rows are listed and dropped from the
   query (you can't price what you can't identify).

2. **Fan out** — `kc_bom_price` issues parallel queries to all four
   distributors (the pricer handles the rate-limit + cache logic
   internally). Returns one row per part with per-distributor
   `unit_price` / `stock` / `lifecycle`.

3. **Aggregate** — for the requested `--quantity`, compute the
   cheapest distributor mix. Splitting across 4 distributors usually
   loses to single-source shipping + minimums; the report shows BOTH
   the cheapest-per-line price AND the cheapest-realistic-mix price.

4. **Surface lifecycle warnings** — any `not_recommended_for_new_design`
   or `obsolete` part triggers a section header. Offer to run
   `/explore-placements` or suggest a `kc_part_alternates` query
   per affected part.

5. **JLCPCB assembly call-out** — if `--prefer jlcpcb-assembly` is
   set, segregate "Basic" parts (no per-part setup fee) from
   "Extended" ($3 setup each). Boards with many "Extended" parts may
   want substitution for cost — surface that explicitly.

## Output shape

```text
BOM-PRICE @qty=10 region=us
  Total: $42.18 (cheapest-mix, Mouser + JLCPCB)
        $38.91 (cheapest-per-line, 4 distributors)

  10 parts priced, 0 missing MPN, 0 obsolete, 1 NRND

  R1 R2 (10k 0603)        $0.012/u (Mouser, 50k in stock)
  C1 C2 C3 (100nF 0603)   $0.008/u (JLCPCB Basic, in stock)
  U1 (ESP32-S3-WROOM-1)   $3.20/u (Mouser, 1200 in stock) [NRND — consider ESP32-S3-WROOM-1-N4 alternate]
  ...
```

## Anti-patterns

- **Don't quote stale prices without saying so.** When the pricer
  serves cached data because a distributor 429'd, the report
  surfaces `(cached 3d ago)` next to each line.
- **Don't pick "cheapest" without surfacing shipping cost.** The
  cheapest mix often includes a $20 Digi-Key shipment for one
  $0.30 part. Aggregate cost matters more than per-line price.
