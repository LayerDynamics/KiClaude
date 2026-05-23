import type { Board, FabTarget } from '../api/client'
import { EnumCell, NumberCell } from './fields'

const FAB_TARGETS = ['jlcpcb', 'oshpark', 'pcbway', 'generic'] as const
type FabTargetName = (typeof FAB_TARGETS)[number]

/** Defaults match the Pydantic FabTarget. */
const DEFAULT_FAB: FabTarget = {
  name: 'jlcpcb',
  min_trace_mm: 0.127,
  min_space_mm: 0.127,
  min_drill_mm: 0.2,
  min_annular_ring_mm: 0.13,
  layer_count: 2,
}

interface FabFormProps {
  board: Board
  onChange: (next: Board) => void
}

/**
 * Structured editor for `Board.fab` (SPEC-1 G3, FR-3): the fab vendor and
 * its five capability fields the synthesizer / DRC respect.
 */
export function FabForm({ board, onChange }: FabFormProps) {
  const fab: FabTarget = board.fab ?? DEFAULT_FAB

  const update = (patch: Partial<FabTarget>) => {
    onChange({ ...board, fab: { ...fab, ...patch } })
  }

  return (
    <section className="form-section" aria-label="Fab target">
      <header className="form-section__header">
        <h3 className="form-section__title">Fab target</h3>
      </header>
      <div className="form-stackup-scalars">
        <label className="form-scalar">
          <span className="form-scalar__label">Vendor</span>
          <EnumCell<FabTargetName>
            value={fab.name as FabTargetName}
            options={FAB_TARGETS}
            ariaLabel="fab-vendor"
            onChange={(value) => update({ name: value ?? 'jlcpcb' })}
          />
        </label>
        <label className="form-scalar">
          <span className="form-scalar__label">Layer count</span>
          <NumberCell
            value={fab.layer_count}
            step={1}
            min={2}
            ariaLabel="fab-layer-count"
            onChange={(value) => update({ layer_count: value ?? 2 })}
          />
        </label>
        <label className="form-scalar">
          <span className="form-scalar__label">Min trace (mm)</span>
          <NumberCell
            value={fab.min_trace_mm}
            step={0.001}
            min={0}
            ariaLabel="fab-min-trace-mm"
            onChange={(value) => update({ min_trace_mm: value ?? 0 })}
          />
        </label>
        <label className="form-scalar">
          <span className="form-scalar__label">Min space (mm)</span>
          <NumberCell
            value={fab.min_space_mm}
            step={0.001}
            min={0}
            ariaLabel="fab-min-space-mm"
            onChange={(value) => update({ min_space_mm: value ?? 0 })}
          />
        </label>
        <label className="form-scalar">
          <span className="form-scalar__label">Min drill (mm)</span>
          <NumberCell
            value={fab.min_drill_mm}
            step={0.01}
            min={0}
            ariaLabel="fab-min-drill-mm"
            onChange={(value) => update({ min_drill_mm: value ?? 0 })}
          />
        </label>
        <label className="form-scalar">
          <span className="form-scalar__label">Min annular ring (mm)</span>
          <NumberCell
            value={fab.min_annular_ring_mm}
            step={0.01}
            min={0}
            ariaLabel="fab-min-annular-ring-mm"
            onChange={(value) => update({ min_annular_ring_mm: value ?? 0 })}
          />
        </label>
      </div>
    </section>
  )
}
