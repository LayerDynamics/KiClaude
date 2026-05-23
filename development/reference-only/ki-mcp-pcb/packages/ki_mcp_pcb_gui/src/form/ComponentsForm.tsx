import type { Board, Component } from '../api/client'
import {
  EnumCell,
  NullableTextCell,
  NumberCell,
  StringListCell,
  TextCell,
} from './fields'

const PARTITIONS = ['analog', 'digital', 'rf', 'power', 'isolated'] as const
type Partition = (typeof PARTITIONS)[number]

interface ComponentsFormProps {
  board: Board
  onChange: (next: Board) => void
}

/**
 * Structured editor for `Board.components` (SPEC-1 G3, FR-3).
 *
 * A controlled component — emits the full next `Board` on every edit. The
 * parent (`BoardForm`) owns persistence through `useCirWriter`.
 */
export function ComponentsForm({ board, onChange }: ComponentsFormProps) {
  const components = board.components ?? []

  const updateAt = (index: number, patch: Partial<Component>) => {
    const next = components.map((component, i) =>
      i === index ? { ...component, ...patch } : component,
    )
    onChange({ ...board, components: next })
  }

  const removeAt = (index: number) => {
    onChange({
      ...board,
      components: components.filter((_, i) => i !== index),
    })
  }

  const addRow = () => {
    const fresh: Component = {
      refdes: '',
      mpn: '',
      is_bridge: false,
      decoupling_pins: [],
    }
    onChange({ ...board, components: [...components, fresh] })
  }

  return (
    <section className="form-section" aria-label="Components">
      <header className="form-section__header">
        <h3 className="form-section__title">Components</h3>
        <button type="button" className="form-add" onClick={addRow}>
          + Add component
        </button>
      </header>
      <div className="form-table-wrap">
        <table className="form-table">
          <thead>
            <tr>
              <th>Refdes</th>
              <th>MPN</th>
              <th>Value</th>
              <th>Partition</th>
              <th>Decoupling pins</th>
              <th>BGA pitch (mm)</th>
              <th aria-label="Remove" />
            </tr>
          </thead>
          <tbody>
            {components.length === 0 && (
              <tr>
                <td colSpan={7} className="form-empty">
                  No components yet — click + Add component.
                </td>
              </tr>
            )}
            {components.map((component, index) => (
              <tr key={index}>
                <td>
                  <TextCell
                    value={component.refdes}
                    ariaLabel={`refdes-${index}`}
                    placeholder="U1"
                    onChange={(value) => updateAt(index, { refdes: value })}
                  />
                </td>
                <td>
                  <TextCell
                    value={component.mpn}
                    ariaLabel={`mpn-${index}`}
                    placeholder="ATSAMD21G18A-AU"
                    onChange={(value) => updateAt(index, { mpn: value })}
                  />
                </td>
                <td>
                  <NullableTextCell
                    value={component.value}
                    ariaLabel={`value-${index}`}
                    onChange={(value) => updateAt(index, { value })}
                  />
                </td>
                <td>
                  <EnumCell<Partition>
                    value={component.partition as Partition | null | undefined}
                    options={PARTITIONS}
                    allowEmpty
                    ariaLabel={`partition-${index}`}
                    onChange={(value) => updateAt(index, { partition: value })}
                  />
                </td>
                <td>
                  <StringListCell
                    value={component.decoupling_pins}
                    ariaLabel={`decoupling-pins-${index}`}
                    placeholder="1, 2, 3"
                    onChange={(value) =>
                      updateAt(index, { decoupling_pins: value })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={component.bga_pitch_mm}
                    step={0.05}
                    min={0}
                    ariaLabel={`bga-pitch-${index}`}
                    onChange={(value) =>
                      updateAt(index, { bga_pitch_mm: value })
                    }
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="form-remove"
                    aria-label={`remove-${index}`}
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
