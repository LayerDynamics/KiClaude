import type { Board, Net } from '../api/client'
import {
  EnumCell,
  NullableTextCell,
  NumberCell,
  StringListCell,
  TextCell,
} from './fields'

const NET_CLASSES = [
  'signal',
  'power',
  'ground',
  'high_speed',
  'differential',
  'rf',
  'analog',
] as const
type NetClass = (typeof NET_CLASSES)[number]

const TOPOLOGIES = [
  'point_to_point',
  'fly_by',
  't_branch',
  'star',
] as const
type Topology = (typeof TOPOLOGIES)[number]

interface NetsFormProps {
  board: Board
  onChange: (next: Board) => void
}

/**
 * Structured editor for `Board.nets` (SPEC-1 G3, FR-3) covering the M2–M4
 * fields the pipeline actually consumes — net class, members, the
 * high-speed knobs (target_impedance_ohm / diff_pair_with / cpwg_gap_mm /
 * trace geometry / reference_plane) and the DDR `topology` + `fly_by_order`.
 */
export function NetsForm({ board, onChange }: NetsFormProps) {
  const nets = board.nets ?? []

  const updateAt = (index: number, patch: Partial<Net>) => {
    const next = nets.map((net, i) =>
      i === index ? { ...net, ...patch } : net,
    )
    onChange({ ...board, nets: next })
  }

  const removeAt = (index: number) => {
    onChange({ ...board, nets: nets.filter((_, i) => i !== index) })
  }

  const addRow = () => {
    const fresh: Net = {
      name: '',
      net_class: 'signal',
      members: [],
      cross_partition_ok: false,
    }
    onChange({ ...board, nets: [...nets, fresh] })
  }

  return (
    <section className="form-section" aria-label="Nets">
      <header className="form-section__header">
        <h3 className="form-section__title">Nets</h3>
        <button type="button" className="form-add" onClick={addRow}>
          + Add net
        </button>
      </header>
      <div className="form-table-wrap">
        <table className="form-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Net class</th>
              <th>Members</th>
              <th>Power rail</th>
              <th>Length-match grp</th>
              <th>Target Zo (Ω)</th>
              <th>Diff pair with</th>
              <th>CPWG gap (mm)</th>
              <th>Trace w (mm)</th>
              <th>Trace s (mm)</th>
              <th>Ref plane</th>
              <th>Topology</th>
              <th>Fly-by order</th>
              <th aria-label="Remove" />
            </tr>
          </thead>
          <tbody>
            {nets.length === 0 && (
              <tr>
                <td colSpan={14} className="form-empty">
                  No nets yet — click + Add net.
                </td>
              </tr>
            )}
            {nets.map((net, index) => (
              <tr key={index}>
                <td>
                  <TextCell
                    value={net.name}
                    ariaLabel={`net-name-${index}`}
                    placeholder="3V3"
                    onChange={(value) => updateAt(index, { name: value })}
                  />
                </td>
                <td>
                  <EnumCell<NetClass>
                    value={net.net_class as NetClass}
                    options={NET_CLASSES}
                    ariaLabel={`net-class-${index}`}
                    onChange={(value) =>
                      updateAt(index, { net_class: value ?? 'signal' })
                    }
                  />
                </td>
                <td>
                  <StringListCell
                    value={net.members}
                    ariaLabel={`net-members-${index}`}
                    placeholder="U1.1, U2.5"
                    onChange={(value) => updateAt(index, { members: value })}
                  />
                </td>
                <td>
                  <NullableTextCell
                    value={net.power_rail}
                    ariaLabel={`net-power-rail-${index}`}
                    placeholder="3V3"
                    onChange={(value) => updateAt(index, { power_rail: value })}
                  />
                </td>
                <td>
                  <NullableTextCell
                    value={net.length_match_group}
                    ariaLabel={`net-lmg-${index}`}
                    placeholder="ddr_addr"
                    onChange={(value) =>
                      updateAt(index, { length_match_group: value })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={net.target_impedance_ohm}
                    step={0.5}
                    min={0}
                    ariaLabel={`net-zo-${index}`}
                    onChange={(value) =>
                      updateAt(index, { target_impedance_ohm: value })
                    }
                  />
                </td>
                <td>
                  <NullableTextCell
                    value={net.diff_pair_with}
                    ariaLabel={`net-diff-pair-${index}`}
                    onChange={(value) =>
                      updateAt(index, { diff_pair_with: value })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={net.cpwg_gap_mm}
                    step={0.01}
                    min={0}
                    ariaLabel={`net-cpwg-gap-${index}`}
                    onChange={(value) =>
                      updateAt(index, { cpwg_gap_mm: value })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={net.trace_width_mm}
                    step={0.01}
                    min={0}
                    ariaLabel={`net-trace-width-${index}`}
                    onChange={(value) =>
                      updateAt(index, { trace_width_mm: value })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={net.trace_spacing_mm}
                    step={0.01}
                    min={0}
                    ariaLabel={`net-trace-spacing-${index}`}
                    onChange={(value) =>
                      updateAt(index, { trace_spacing_mm: value })
                    }
                  />
                </td>
                <td>
                  <NullableTextCell
                    value={net.reference_plane}
                    ariaLabel={`net-ref-plane-${index}`}
                    placeholder="In1.Cu"
                    onChange={(value) =>
                      updateAt(index, { reference_plane: value })
                    }
                  />
                </td>
                <td>
                  <EnumCell<Topology>
                    value={net.topology as Topology | null | undefined}
                    options={TOPOLOGIES}
                    allowEmpty
                    ariaLabel={`net-topology-${index}`}
                    onChange={(value) => updateAt(index, { topology: value })}
                  />
                </td>
                <td>
                  <StringListCell
                    value={net.fly_by_order}
                    ariaLabel={`net-fly-by-order-${index}`}
                    placeholder="U_MCU, U_RAM1, U_TERM"
                    onChange={(value) =>
                      updateAt(index, { fly_by_order: value })
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="form-remove"
                    aria-label={`net-remove-${index}`}
                    onClick={() => removeAt(index)}
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
