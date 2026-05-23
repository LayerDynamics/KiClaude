import type { Board, Layer, Stackup } from '../api/client'
import {
  EnumCell,
  NullableTextCell,
  NumberCell,
  StringListCell,
  TextCell,
} from './fields'

const LAYER_KINDS = [
  'copper',
  'dielectric',
  'soldermask',
  'silkscreen',
  'paste',
] as const
type LayerKind = (typeof LAYER_KINDS)[number]

/** Defaults for a freshly added stackup. Mirrors the Pydantic Stackup default. */
const DEFAULT_STACKUP: Stackup = {
  layers: [],
  finished_thickness_mm: 1.6,
  controlled_impedance: false,
  power_plane_layers: [],
}

interface StackupFormProps {
  board: Board
  onChange: (next: Board) => void
}

/**
 * Structured editor for `Board.stackup` (SPEC-1 G3, FR-3).
 *
 * Supports the scalar fields (`finished_thickness_mm`,
 * `controlled_impedance`, `power_plane_layers`) and full editing of the
 * `layers` list — add, remove, reorder, plus per-layer kind / name /
 * thickness / material / Er — so form mode is not strictly less capable
 * than text mode (FR-4).
 */
export function StackupForm({ board, onChange }: StackupFormProps) {
  const stackup: Stackup = board.stackup ?? DEFAULT_STACKUP
  const layers = stackup.layers

  const updateStackup = (patch: Partial<Stackup>) => {
    onChange({ ...board, stackup: { ...stackup, ...patch } })
  }

  const updateLayerAt = (index: number, patch: Partial<Layer>) => {
    const next = layers.map((layer, i) =>
      i === index ? { ...layer, ...patch } : layer,
    )
    updateStackup({ layers: next })
  }

  const removeLayerAt = (index: number) => {
    updateStackup({ layers: layers.filter((_, i) => i !== index) })
  }

  const swap = (a: number, b: number) => {
    if (a < 0 || b < 0 || a >= layers.length || b >= layers.length) return
    const next = [...layers]
    ;[next[a], next[b]] = [next[b], next[a]]
    updateStackup({ layers: next })
  }

  const addLayer = () => {
    const fresh: Layer = {
      name: '',
      kind: 'copper',
      thickness_mm: 0.035,
      material: null,
      er: null,
    }
    updateStackup({ layers: [...layers, fresh] })
  }

  return (
    <section className="form-section" aria-label="Stackup">
      <header className="form-section__header">
        <h3 className="form-section__title">Stackup</h3>
        <button type="button" className="form-add" onClick={addLayer}>
          + Add layer
        </button>
      </header>

      <div className="form-stackup-scalars">
        <label className="form-scalar">
          <span className="form-scalar__label">Finished thickness (mm)</span>
          <NumberCell
            value={stackup.finished_thickness_mm}
            step={0.05}
            min={0}
            ariaLabel="finished-thickness-mm"
            onChange={(value) =>
              updateStackup({
                finished_thickness_mm: value ?? 0,
              })
            }
          />
        </label>
        <label className="form-scalar">
          <span className="form-scalar__label">Controlled impedance</span>
          <input
            type="checkbox"
            aria-label="controlled-impedance"
            checked={stackup.controlled_impedance}
            onChange={(event) =>
              updateStackup({ controlled_impedance: event.target.checked })
            }
          />
        </label>
        <label className="form-scalar form-scalar--wide">
          <span className="form-scalar__label">Power plane layers</span>
          <StringListCell
            value={stackup.power_plane_layers}
            ariaLabel="power-plane-layers"
            placeholder="In1.Cu, In2.Cu"
            onChange={(value) => updateStackup({ power_plane_layers: value })}
          />
        </label>
      </div>

      <div className="form-table-wrap">
        <table className="form-table">
          <thead>
            <tr>
              <th aria-label="Order" />
              <th>Name</th>
              <th>Kind</th>
              <th>Thickness (mm)</th>
              <th>Material</th>
              <th>Er</th>
              <th aria-label="Remove" />
            </tr>
          </thead>
          <tbody>
            {layers.length === 0 && (
              <tr>
                <td colSpan={7} className="form-empty">
                  No layers yet — click + Add layer.
                </td>
              </tr>
            )}
            {layers.map((layer, index) => (
              <tr key={index}>
                <td className="form-stackup-order">
                  <button
                    type="button"
                    className="form-reorder"
                    aria-label={`layer-up-${index}`}
                    onClick={() => swap(index, index - 1)}
                    disabled={index === 0}
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    className="form-reorder"
                    aria-label={`layer-down-${index}`}
                    onClick={() => swap(index, index + 1)}
                    disabled={index === layers.length - 1}
                  >
                    ↓
                  </button>
                </td>
                <td>
                  <TextCell
                    value={layer.name}
                    ariaLabel={`layer-name-${index}`}
                    placeholder="F.Cu"
                    onChange={(value) => updateLayerAt(index, { name: value })}
                  />
                </td>
                <td>
                  <EnumCell<LayerKind>
                    value={layer.kind as LayerKind}
                    options={LAYER_KINDS}
                    ariaLabel={`layer-kind-${index}`}
                    onChange={(value) =>
                      updateLayerAt(index, { kind: value ?? 'copper' })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={layer.thickness_mm}
                    step={0.001}
                    min={0}
                    ariaLabel={`layer-thickness-${index}`}
                    onChange={(value) =>
                      updateLayerAt(index, { thickness_mm: value ?? 0 })
                    }
                  />
                </td>
                <td>
                  <NullableTextCell
                    value={layer.material}
                    ariaLabel={`layer-material-${index}`}
                    placeholder="FR-4"
                    onChange={(value) =>
                      updateLayerAt(index, { material: value })
                    }
                  />
                </td>
                <td>
                  <NumberCell
                    value={layer.er}
                    step={0.01}
                    min={0}
                    ariaLabel={`layer-er-${index}`}
                    onChange={(value) => updateLayerAt(index, { er: value })}
                  />
                </td>
                <td>
                  <button
                    type="button"
                    className="form-remove"
                    aria-label={`layer-remove-${index}`}
                    onClick={() => removeLayerAt(index)}
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
