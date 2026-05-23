//! Schematic view: sheets, symbols, wires, labels.
//!
//! The KCIR model for a `.kicad_sch`. M0 shipped sketches of the shape;
//! M1-R-01 fills the field set in lockstep with the parser at
//! [`crate::format::v9::sch`].

use serde::{Deserialize, Serialize};

use super::nets::PadRef;

/// A schematic view of a [`Project`](super::Project) — a forest of sheets.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Schematic {
    /// All sheets in declaration order. `sheets[0]` is conventionally
    /// the root; multi-sheet hierarchy resolution lands in M1-R-05.
    pub sheets: Vec<Sheet>,
    pub symbols: Vec<SymbolInstance>,
    pub wires: Vec<Wire>,
    pub junctions: Vec<Junction>,
    pub labels: Vec<Label>,
    pub no_connects: Vec<NoConnect>,
    pub buses: Vec<Bus>,
    /// Library symbol definitions inlined at the top of each
    /// `.kicad_sch`. De-duplicated by `lib_id` across sheets.
    pub lib_symbols: Vec<LibSymbol>,
}

/// A single sheet within the schematic hierarchy.
///
/// Top-level (root) sheets have `parent = None`. Sub-sheets reference
/// their parent via uuid. The on-canvas placement fields
/// ([`position_mm`](Self::position_mm) / [`size_mm`](Self::size_mm))
/// are only meaningful for sub-sheets — the root sheet leaves them
/// at zero.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Sheet {
    pub uuid: String,
    pub name: String,
    pub file: String,
    pub parent: Option<String>,
    /// On-canvas position of the sub-sheet block on the parent sheet.
    pub position_mm: (f64, f64),
    /// On-canvas size of the sub-sheet block on the parent sheet.
    pub size_mm: (f64, f64),
    /// Hierarchical pins that connect this sub-sheet to its parent.
    pub pins: Vec<SheetPin>,
}

/// A pin on a sub-sheet block — the in/out connection from the parent
/// sheet to a hierarchical label inside the child sheet.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SheetPin {
    pub uuid: String,
    pub name: String,
    /// `input` | `output` | `bidirectional` | `tri_state` | `passive`.
    pub shape: String,
    pub position_mm: (f64, f64),
    pub rotation_deg: f64,
}

/// An instance of a library symbol placed on a sheet.
///
/// Carries six independent boolean flags (`mirrored`, `in_bom`,
/// `on_board`, `dnp`, `is_power_flag`, `is_power_symbol`) — each
/// surfaces a separate `KiCad`-defined concept that callers query
/// individually, so packing them behind a bitset would only hide the
/// names.
#[allow(clippy::struct_excessive_bools)]
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SymbolInstance {
    pub uuid: String,
    pub sheet_uuid: String,
    pub lib_id: String,
    pub refdes: String,
    pub value: String,
    pub footprint: String,
    pub mpn: String,
    pub datasheet: String,
    pub position_mm: (f64, f64),
    pub rotation_deg: f64,
    pub mirrored: bool,
    /// Multi-unit symbols (e.g. a quad-NAND has 4 units); 1-based. The
    /// emitter writes `(unit 1)` for single-unit symbols.
    pub unit: i32,
    /// True if this symbol should appear on the BOM. `KiCad` default: true.
    pub in_bom: bool,
    /// True if this symbol should appear on the PCB netlist. Default: true.
    pub on_board: bool,
    /// "Do Not Populate" — included in the BOM but flagged.
    pub dnp: bool,
    /// True for `power:PWR_FLAG` instances — they're net markers, not
    /// real components. Detected by [`crate::format::v9::sch::map_sch`]
    /// from the symbol's `lib_id`.
    pub is_power_flag: bool,
    /// True if this symbol is a power-net symbol from the `KiCad`
    /// `power` library (e.g. `power:GND`, `power:VCC`). Power symbols
    /// are implicit net labels rather than physical components.
    pub is_power_symbol: bool,
    /// Every `(property "Key" "Value" …)` form on this instance, in
    /// declaration order. Includes the standard fields
    /// (`Reference`, `Value`, `Footprint`, `Datasheet`, `MPN`) — those
    /// are also surfaced as the named fields above for ergonomics.
    pub properties: Vec<SymbolProperty>,
}

/// A `(property "Key" "Value" (at x y rot) (effects …))` form attached
/// to a [`SymbolInstance`] or [`LibSymbol`].
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct SymbolProperty {
    pub key: String,
    pub value: String,
    /// Position offset on the sheet. Zero if the property has no `(at …)`.
    pub position_mm: (f64, f64),
    pub rotation_deg: f64,
    /// True if the property is rendered as hidden in the editor.
    pub hide: bool,
}

/// A library symbol definition inlined at the top of a `.kicad_sch`.
///
/// `KiCad` bundles every symbol definition used by a sheet so the file
/// is self-contained even if the originating library is unavailable.
/// M1-R-04 reads the on-disk `.kicad_sym` libraries; this struct
/// captures the inlined cache so a round-trip can preserve symbols
/// that no longer exist on disk.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct LibSymbol {
    pub lib_id: String,
    pub properties: Vec<SymbolProperty>,
    /// True if the symbol comes from the `power:` library namespace.
    pub is_power: bool,
}

/// A straight wire segment between two points on a sheet.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Wire {
    pub uuid: String,
    pub sheet_uuid: String,
    pub points_mm: Vec<(f64, f64)>,
}

/// A wire junction (T or +) — required when a wire crosses or branches.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Junction {
    pub uuid: String,
    pub sheet_uuid: String,
    pub position_mm: (f64, f64),
}

/// A net label attached to a wire.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Label {
    pub uuid: String,
    pub sheet_uuid: String,
    pub kind: LabelKind,
    pub text: String,
    pub position_mm: (f64, f64),
    pub rotation_deg: f64,
    /// `input` | `output` | `bidirectional` | `tri_state` | `passive`.
    /// Set for global / hierarchical labels; empty for local labels.
    pub shape: String,
}

/// Discriminator for the four `KiCad` label flavors.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(
    Debug, Clone, Copy, Default, PartialEq, Eq, Hash, Ord, PartialOrd, Serialize, Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum LabelKind {
    #[default]
    Local,
    Global,
    Hierarchical,
    Power,
}

/// A no-connect marker on a symbol pin or floating wire end.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct NoConnect {
    pub uuid: String,
    pub sheet_uuid: String,
    /// On-sheet position of the marker.
    pub position_mm: (f64, f64),
    /// Optional reference to the pad the no-connect attaches to.
    /// Populated by M1-R-05's connectivity pass; empty until then.
    pub at: PadRef,
}

/// A schematic bus — a labeled group of related nets.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Bus {
    pub uuid: String,
    pub sheet_uuid: String,
    pub name: String,
    pub points_mm: Vec<(f64, f64)>,
    pub members: Vec<String>,
}
