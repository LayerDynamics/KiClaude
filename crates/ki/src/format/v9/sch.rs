//! `.kicad_sch` S-expression → KCIR [`Schematic`].
//!
//! The mapper walks a parsed [`SNode`] tree whose root is `kicad_sch`
//! and lifts:
//!
//! - The sheet metadata (uuid, paper, title block) into [`Sheet`] +
//!   [`crate::kcir::ProjectMetadata`].
//! - `(lib_symbols (symbol …))` cache → [`LibSymbol`].
//! - `(symbol …)` placement instances → [`SymbolInstance`] with full
//!   property preservation; instances whose `lib_id` is `power:PWR_FLAG`
//!   get `is_power_flag = true`; instances under the `power:` namespace
//!   get `is_power_symbol = true`.
//! - `(wire …)` segments → [`Wire`] (pts → `points_mm`).
//! - `(junction …)` markers → [`Junction`].
//! - `(label …)` / `(global_label …)` / `(hierarchical_label …)` →
//!   [`Label`] (kind = Local / Global / Hierarchical). Power labels
//!   are represented as power-symbol instances (`KiCad` convention)
//!   and surface as [`SymbolInstance`] with `is_power_symbol = true`.
//! - `(no_connect …)` markers → [`NoConnect`].
//! - `(sheet …)` blocks (sub-sheet references) → [`Sheet`] entries
//!   with `parent = Some(root.uuid)`, including their `(pin …)`
//!   children as [`SheetPin`]s.
//! - `(bus …)` segments → [`Bus`].
//!
//! Forms we don't yet model (e.g. graphical annotations, embedded
//! files) are silently ignored. The parsed [`SNode`] tree remains the
//! canonical store; KCIR is the editing view.

use super::sexpr_helpers::{
    atom_f64, atom_str, body_children, collect_xy_points, find_child, find_children,
};
use super::symbols::{parse_lib_symbol, parse_property, parse_sheet_pin, read_at, read_yes_no};
use crate::kcir::{
    Bus, Junction, Label, LabelKind, LibSymbol, NoConnect, Schematic, Sheet, SheetPin,
    SymbolInstance, SymbolProperty, Wire,
};
use crate::sexpr::SNode;

/// Result of mapping a single `.kicad_sch` file.
///
/// The root `Sheet` is what the caller stitches into the project's
/// [`Schematic::sheets`]; the per-sheet entities are returned flat
/// so the caller can extend an existing [`Schematic`] across files.
#[derive(Debug, Clone, Default)]
pub struct ParsedSheet {
    /// The sheet itself (uuid + title-block metadata). Other top-level
    /// `.kicad_pro`-seeded fields (`name`, `file`, `parent`) are left
    /// to the caller to fill from project metadata.
    pub sheet: Sheet,
    /// Sub-sheet references found in the file. Each entry's `parent`
    /// is set to `sheet.uuid`.
    pub sub_sheets: Vec<Sheet>,
    /// Inlined library symbol cache.
    pub lib_symbols: Vec<LibSymbol>,
    /// Placement instances of library symbols.
    pub symbols: Vec<SymbolInstance>,
    pub wires: Vec<Wire>,
    pub junctions: Vec<Junction>,
    pub labels: Vec<Label>,
    pub no_connects: Vec<NoConnect>,
    pub buses: Vec<Bus>,
}

/// Map a parsed `(kicad_sch …)` form to a [`ParsedSheet`].
///
/// # Errors
///
/// Returns `Err(String)` when the root form's head symbol isn't
/// `kicad_sch`.
pub fn map_sch(root: &SNode) -> Result<ParsedSheet, String> {
    if root.head_symbol() != Some("kicad_sch") {
        return Err(format!(
            "expected (kicad_sch …) root, got {:?}",
            root.head_symbol()
        ));
    }
    let mut out = ParsedSheet::default();
    map_sheet_metadata(root, &mut out.sheet);
    let sheet_uuid = out.sheet.uuid.clone();
    out.lib_symbols = map_lib_symbols(root);
    out.symbols = map_symbol_instances(root, &sheet_uuid);
    out.wires = map_wires(root, &sheet_uuid);
    out.junctions = map_junctions(root, &sheet_uuid);
    out.labels = map_labels(root, &sheet_uuid);
    out.no_connects = map_no_connects(root, &sheet_uuid);
    out.buses = map_buses(root, &sheet_uuid);
    out.sub_sheets = map_sub_sheets(root, &sheet_uuid);
    Ok(out)
}

/// Merge a [`ParsedSheet`] into an existing [`Schematic`].
///
/// `seed` is the [`Sheet`] entry the caller already created for this
/// file (typically from `.kicad_pro`'s `top_level_sheets` so the
/// `file` / `parent` fields are correct). The parsed sheet's
/// position/size/uuid override the seed's defaults.
pub fn merge_into_schematic(parsed: ParsedSheet, schematic: &mut Schematic, seed: Option<Sheet>) {
    let parsed_sheet = parsed.sheet;
    let parsed_uuid = parsed_sheet.uuid.clone();

    // Patch or insert the root sheet entry.
    if let Some(seed) = seed {
        if let Some(existing) = schematic
            .sheets
            .iter_mut()
            .find(|s| s.uuid == seed.uuid || s.file == seed.file)
        {
            if !parsed_uuid.is_empty() {
                existing.uuid.clone_from(&parsed_uuid);
            }
            existing.position_mm = parsed_sheet.position_mm;
            existing.size_mm = parsed_sheet.size_mm;
            existing.pins = parsed_sheet.pins;
        } else {
            let mut sheet = seed;
            if !parsed_uuid.is_empty() {
                sheet.uuid.clone_from(&parsed_uuid);
            }
            sheet.position_mm = parsed_sheet.position_mm;
            sheet.size_mm = parsed_sheet.size_mm;
            sheet.pins = parsed_sheet.pins;
            schematic.sheets.push(sheet);
        }
    } else if schematic.sheets.iter().all(|s| s.uuid != parsed_uuid) {
        schematic.sheets.push(parsed_sheet);
    }

    // Append sub-sheet references, preserving any already present.
    for sub in parsed.sub_sheets {
        if schematic.sheets.iter().any(|s| s.uuid == sub.uuid) {
            continue;
        }
        schematic.sheets.push(sub);
    }

    // Dedup library symbols by lib_id (last writer wins — sheets in a
    // multi-sheet project sometimes diverge on cached metadata).
    for lib in parsed.lib_symbols {
        if let Some(existing) = schematic
            .lib_symbols
            .iter_mut()
            .find(|l| l.lib_id == lib.lib_id)
        {
            *existing = lib;
        } else {
            schematic.lib_symbols.push(lib);
        }
    }

    schematic.symbols.extend(parsed.symbols);
    schematic.wires.extend(parsed.wires);
    schematic.junctions.extend(parsed.junctions);
    schematic.labels.extend(parsed.labels);
    schematic.no_connects.extend(parsed.no_connects);
    schematic.buses.extend(parsed.buses);
}

fn map_sheet_metadata(root: &SNode, sheet: &mut Sheet) {
    if let Some(uuid_form) = find_child(root, "uuid") {
        sheet.uuid = body_children(uuid_form)
            .next()
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
    }
}

fn map_lib_symbols(root: &SNode) -> Vec<LibSymbol> {
    let Some(lib_block) = find_child(root, "lib_symbols") else {
        return Vec::new();
    };
    find_children(lib_block, "symbol")
        .iter()
        .map(|s| parse_lib_symbol(s))
        .collect()
}

#[allow(clippy::similar_names)] // x/y/rot is the natural pcb.rs idiom.
fn map_symbol_instances(root: &SNode, sheet_uuid: &str) -> Vec<SymbolInstance> {
    let mut out = Vec::new();
    for node in find_children(root, "symbol") {
        // `(symbol "LIB:NAME" …)` placement form. The `(lib_symbols
        // (symbol "name" …))` definitions live inside `lib_symbols`
        // and were handled separately — those won't reach this loop
        // because `find_children` only looks at direct children.
        let lib_id = read_lib_id(node);
        let uuid = read_uuid(node);
        let (x, y, rot) = read_at(node);
        let unit = find_child(node, "unit")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .and_then(|s| s.parse::<i32>().ok())
            .unwrap_or(1);
        let mirrored = find_child(node, "mirror").is_some();
        let in_bom = read_yes_no(node, "in_bom", true);
        let on_board = read_yes_no(node, "on_board", true);
        let dnp = read_yes_no(node, "dnp", false);

        let properties: Vec<SymbolProperty> = find_children(node, "property")
            .iter()
            .map(|p| parse_property(p))
            .collect();

        let lookup = |key: &str| {
            properties
                .iter()
                .find(|p| p.key == key)
                .map(|p| p.value.clone())
                .unwrap_or_default()
        };
        let refdes = lookup("Reference");
        let value = lookup("Value");
        let footprint = lookup("Footprint");
        let mpn = if lookup("MPN").is_empty() {
            lookup("Manufacturer Part Number")
        } else {
            lookup("MPN")
        };
        let datasheet = lookup("Datasheet");

        let is_power_symbol = lib_id.starts_with("power:");
        let is_power_flag = lib_id == "power:PWR_FLAG"
            || lib_id.eq_ignore_ascii_case("power:pwr_flag")
            || lib_id.ends_with(":PWR_FLAG");

        out.push(SymbolInstance {
            uuid,
            sheet_uuid: sheet_uuid.to_string(),
            lib_id,
            refdes,
            value,
            footprint,
            mpn,
            datasheet,
            position_mm: (x, y),
            rotation_deg: rot,
            mirrored,
            unit,
            in_bom,
            on_board,
            dnp,
            is_power_flag,
            is_power_symbol,
            properties,
        });
    }
    out
}

fn map_wires(root: &SNode, sheet_uuid: &str) -> Vec<Wire> {
    let mut out = Vec::new();
    for node in find_children(root, "wire") {
        let points_mm = find_child(node, "pts")
            .map(collect_xy_points)
            .unwrap_or_default();
        let uuid = read_uuid(node);
        out.push(Wire {
            uuid,
            sheet_uuid: sheet_uuid.to_string(),
            points_mm,
        });
    }
    out
}

fn map_junctions(root: &SNode, sheet_uuid: &str) -> Vec<Junction> {
    let mut out = Vec::new();
    for node in find_children(root, "junction") {
        let (x, y, _) = read_at(node);
        let uuid = read_uuid(node);
        out.push(Junction {
            uuid,
            sheet_uuid: sheet_uuid.to_string(),
            position_mm: (x, y),
        });
    }
    out
}

#[allow(clippy::similar_names)] // x/y/rot is the natural pcb.rs idiom.
fn map_labels(root: &SNode, sheet_uuid: &str) -> Vec<Label> {
    let mut out = Vec::new();
    let kinds = [
        ("label", LabelKind::Local),
        ("global_label", LabelKind::Global),
        ("hierarchical_label", LabelKind::Hierarchical),
        // Some KiCad-derived files emit `power_label` for the synthetic
        // power-label form. The mainline KiCad 9 representation uses a
        // `power:`-namespaced symbol instance instead — that path is
        // handled by `map_symbol_instances` (is_power_symbol = true).
        ("power_label", LabelKind::Power),
    ];
    for (head, kind) in kinds {
        for node in find_children(root, head) {
            let text = body_children(node)
                .next()
                .and_then(atom_str)
                .unwrap_or("")
                .to_string();
            let (x, y, rot) = read_at(node);
            let uuid = read_uuid(node);
            let shape = find_child(node, "shape")
                .and_then(|n| body_children(n).next())
                .and_then(atom_str)
                .unwrap_or("")
                .to_string();
            out.push(Label {
                uuid,
                sheet_uuid: sheet_uuid.to_string(),
                kind,
                text,
                position_mm: (x, y),
                rotation_deg: rot,
                shape,
            });
        }
    }
    out
}

fn map_no_connects(root: &SNode, sheet_uuid: &str) -> Vec<NoConnect> {
    let mut out = Vec::new();
    for node in find_children(root, "no_connect") {
        let (x, y, _) = read_at(node);
        let uuid = read_uuid(node);
        out.push(NoConnect {
            uuid,
            sheet_uuid: sheet_uuid.to_string(),
            position_mm: (x, y),
            at: crate::kcir::PadRef::default(),
        });
    }
    out
}

fn map_buses(root: &SNode, sheet_uuid: &str) -> Vec<Bus> {
    let mut out = Vec::new();
    for node in find_children(root, "bus") {
        let points_mm = find_child(node, "pts")
            .map(collect_xy_points)
            .unwrap_or_default();
        let uuid = read_uuid(node);
        // (bus_alias "PORT" (members A B C)) is a separate top-level
        // form; (bus …) by itself is a wire of bus type.
        out.push(Bus {
            uuid,
            sheet_uuid: sheet_uuid.to_string(),
            name: String::new(),
            points_mm,
            members: Vec::new(),
        });
    }
    // (bus_alias "NAME" (members A B C …)) — these carry the named
    // bus's member list. We attach the alias to a synthetic Bus entry
    // so the alias survives the round-trip.
    for node in find_children(root, "bus_alias") {
        let name = body_children(node)
            .next()
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        let members = find_child(node, "members")
            .map(|m| {
                body_children(m)
                    .filter_map(atom_str)
                    .map(String::from)
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        out.push(Bus {
            uuid: String::new(),
            sheet_uuid: sheet_uuid.to_string(),
            name,
            points_mm: Vec::new(),
            members,
        });
    }
    out
}

fn map_sub_sheets(root: &SNode, parent_uuid: &str) -> Vec<Sheet> {
    let mut out = Vec::new();
    for node in find_children(root, "sheet") {
        let uuid = read_uuid(node);
        let (origin_x, origin_y, _) = read_at(node);
        let (width, height) = read_size(node);

        // Sheet display name / file are stored as `(property "Sheetname" …)`
        // / `(property "Sheetfile" …)` in KiCad 9. Older files used
        // bare `(name …)` / `(file …)`.
        let mut name = String::new();
        let mut file = String::new();
        for prop in find_children(node, "property") {
            let p = parse_property(prop);
            match p.key.as_str() {
                "Sheetname" | "Sheet name" => name = p.value,
                "Sheetfile" | "Sheet file" => file = p.value,
                _ => {}
            }
        }
        if name.is_empty() {
            if let Some(form) = find_child(node, "name") {
                name = body_children(form)
                    .next()
                    .and_then(atom_str)
                    .unwrap_or("")
                    .to_string();
            }
        }
        if file.is_empty() {
            if let Some(form) = find_child(node, "file") {
                file = body_children(form)
                    .next()
                    .and_then(atom_str)
                    .unwrap_or("")
                    .to_string();
            }
        }

        let pins: Vec<SheetPin> = find_children(node, "pin")
            .iter()
            .map(|p| parse_sheet_pin(p))
            .collect();

        out.push(Sheet {
            uuid,
            name,
            file,
            parent: Some(parent_uuid.to_string()),
            position_mm: (origin_x, origin_y),
            size_mm: (width, height),
            pins,
        });
    }
    out
}

fn read_uuid(parent: &SNode) -> String {
    find_child(parent, "uuid")
        .and_then(|n| body_children(n).next())
        .and_then(atom_str)
        .unwrap_or("")
        .to_string()
}

fn read_lib_id(parent: &SNode) -> String {
    // Modern form: (lib_id "Foo:Bar").
    if let Some(form) = find_child(parent, "lib_id") {
        return body_children(form)
            .next()
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
    }
    // Inline-string form: (symbol "Foo:Bar" …). The lib_id is the
    // first positional child after the head, before any sub-forms.
    body_children(parent)
        .next()
        .and_then(|n| match n {
            SNode::Atom { .. } => atom_str(n),
            SNode::List { .. } => None,
        })
        .unwrap_or("")
        .to_string()
}

fn read_size(parent: &SNode) -> (f64, f64) {
    find_child(parent, "size").map_or((0.0, 0.0), |n| {
        let body: Vec<&SNode> = body_children(n).collect();
        let w = body.first().and_then(|n| atom_f64(n)).unwrap_or(0.0);
        let h = body.get(1).and_then(|n| atom_f64(n)).unwrap_or(0.0);
        (w, h)
    })
}
