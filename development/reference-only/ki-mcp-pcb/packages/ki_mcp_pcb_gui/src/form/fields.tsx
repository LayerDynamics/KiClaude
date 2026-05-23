// Reusable cell editors shared by every form in `src/form/`.
// Each one is a pure controlled input that converts between the
// HTML input model (always strings, possibly empty) and the typed
// CIR field model (typed unions, possibly null).

interface TextCellProps {
  value: string
  onChange: (next: string) => void
  placeholder?: string
  ariaLabel?: string
}

/** Required string cell — backs `refdes`, `mpn`, `Net.name`. */
export function TextCell({ value, onChange, placeholder, ariaLabel }: TextCellProps) {
  return (
    <input
      className="form-cell"
      type="text"
      value={value}
      placeholder={placeholder}
      aria-label={ariaLabel}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

interface NullableTextCellProps {
  value: string | null | undefined
  onChange: (next: string | null) => void
  placeholder?: string
  ariaLabel?: string
}

/** Optional string cell — empty input means `null`. */
export function NullableTextCell({
  value,
  onChange,
  placeholder,
  ariaLabel,
}: NullableTextCellProps) {
  return (
    <input
      className="form-cell"
      type="text"
      value={value ?? ''}
      placeholder={placeholder}
      aria-label={ariaLabel}
      onChange={(event) => {
        const raw = event.target.value
        onChange(raw === '' ? null : raw)
      }}
    />
  )
}

interface NumberCellProps {
  value: number | null | undefined
  onChange: (next: number | null) => void
  step?: number
  min?: number
  ariaLabel?: string
}

/** Optional numeric cell — empty input means `null`; malformed input is ignored. */
export function NumberCell({
  value,
  onChange,
  step = 0.01,
  min,
  ariaLabel,
}: NumberCellProps) {
  return (
    <input
      className="form-cell form-cell--number"
      type="number"
      step={step}
      min={min}
      value={value ?? ''}
      aria-label={ariaLabel}
      onChange={(event) => {
        const raw = event.target.value
        if (raw === '') {
          onChange(null)
          return
        }
        const parsed = Number(raw)
        if (!Number.isNaN(parsed)) onChange(parsed)
      }}
    />
  )
}

interface EnumCellProps<T extends string> {
  value: T | null | undefined
  options: readonly T[]
  onChange: (next: T | null) => void
  allowEmpty?: boolean
  ariaLabel?: string
}

/** Enum-typed cell with an optional empty-selectable `null` value. */
export function EnumCell<T extends string>({
  value,
  options,
  onChange,
  allowEmpty = false,
  ariaLabel,
}: EnumCellProps<T>) {
  return (
    <select
      className="form-cell"
      value={value ?? ''}
      aria-label={ariaLabel}
      onChange={(event) => {
        const raw = event.target.value
        onChange(raw === '' ? null : (raw as T))
      }}
    >
      {allowEmpty && <option value="">—</option>}
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  )
}

interface StringListCellProps {
  value: string[] | undefined
  onChange: (next: string[]) => void
  placeholder?: string
  ariaLabel?: string
}

/**
 * String-list cell as comma-separated text — used for `members`,
 * `decoupling_pins`, `fly_by_order`. Empty/whitespace items are dropped.
 */
export function StringListCell({
  value,
  onChange,
  placeholder,
  ariaLabel,
}: StringListCellProps) {
  const text = (value ?? []).join(', ')
  return (
    <input
      className="form-cell"
      type="text"
      value={text}
      placeholder={placeholder}
      aria-label={ariaLabel}
      onChange={(event) => {
        const items = event.target.value
          .split(',')
          .map((part) => part.trim())
          .filter((part) => part.length > 0)
        onChange(items)
      }}
    />
  )
}
