//! KCIR → `.kicad_pcb` S-expression emitter.
//!
//! Produces a canonical, deterministic text form for a [`Pcb`].
//! The companion [`super::pcb::map_pcb`] reader and this emitter are
//! designed to round-trip byte-identically: if you parse a canonical-form
//! `.kicad_pcb` to KCIR and emit it back, you get the exact same bytes.
//!
//! # Canonical form (M2-R-01/02 contract)
//!
//! Top-level form ordering inside `(kicad_pcb …)`:
//!
//! 1. `(version YYYYMMDD)` and `(generator NAME)` on the same line as the
//!    head — matches the `KiCad 9` visual style.
//! 2. `(general (thickness X))` — one blank line separator.
//! 3. `(paper "X")`.
//! 4. `(layers (ID "NAME" KIND ["PURPOSE"]) …)`.
//! 5. `(setup (pad_to_mask_clearance X) [(solder_mask_min_width X)])`.
//! 6. `(net_class …)` declarations, one per `Pcb::net_classes` entry.
//! 7. `(net 0 "")` followed by `(net N "NAME")` per named net.
//! 8. `(footprint …)` blocks — each emits in this order:
//!    layer, uuid, at, attr (when present), property (Reference, Value,
//!    MPN), pads, inner drawings (`fp_line`/`fp_arc`/etc.), courtyard
//!    (as `fp_poly`), `(model …)` blocks.
//! 9. `(segment …)` tracks — emitted on a single line each.
//! 10. `(via …)` blocks — emitted on a single line each, optional
//!     `blind`/`buried` keyword before `(at …)`, optional `(locked)`.
//! 11. `(zone …)` blocks — each emits: net, `net_name`, layer, uuid,
//!     optional `(hatched)`, `(connect_pads …)`, `(min_thickness …)`,
//!     optional thermal gap/bridge, polygon (outline), polygon… for
//!     each cutout, `(filled_polygon …)` for each precomputed fill.
//! 12. Board outline `(gr_line … (layer "Edge.Cuts"))` — emitted from
//!     `Pcb::outline.points_mm` as consecutive pair-of-points lines.
//! 13. Other `(gr_line/gr_arc/gr_rect/gr_circle/gr_text …)` drawings.
//!
//! Determinism rules:
//! - Indentation: two spaces per nesting level.
//! - One blank line between top-level subforms inside `(kicad_pcb …)`
//!   (matches the `KiCad` 6+ visual style).
//! - Floats are formatted via [`format_float`] — trailing zeros trimmed
//!   but always at least one digit after the decimal point.
//! - Strings are double-quoted and escape `\` and `"`.
//! - Field order inside each form is fixed in the relevant `emit_*` fn.
//!
//! This emitter does NOT preserve free-form `KiCad` formatting from a
//! third-party-written `.kicad_pcb` — kiclaude owns the canonical form.
//! For preserving original bytes of a `KiCad`-IDE-saved file, the
//! caller should keep the source text and write it back verbatim.

use std::collections::BTreeMap;
use std::fmt::Write as _;

use crate::kcir::{
    Drawing, FootprintCourtyard, FootprintInstance, Layer, Model3D, Net, NetClass, Outline, Pad,
    Pcb, Stackup, StackupLayer, StackupLayerKind, Track, Via, Zone,
};

/// Emit a [`Pcb`] as canonical `.kicad_pcb` text.
///
/// Delegates to [`emit_pcb_with_stackup`] with no stackup — preserves the
/// pre-M3 emit shape for callers (golden fixtures, python/wasm bindings)
/// that emit a board without project-level stackup metadata.
#[must_use]
pub fn emit_pcb(pcb: &Pcb) -> String {
    emit_pcb_with_stackup(pcb, None)
}

/// Emit a [`Pcb`] with an optional project-level [`Stackup`].
///
/// When `stackup` is `Some`, a `(stackup …)` form is written inside the
/// `(setup …)` block — the shape `KiCad` 9 expects, per M3-R-01. The
/// emitted layer-type strings (`"copper"`, `"core"`, `"solder_mask"`, …) are
/// chosen so the companion parser
/// [`super::pcb::map_stackup_from_pcb`] reads back identical
/// [`StackupLayerKind`] values.
#[must_use]
pub fn emit_pcb_with_stackup(pcb: &Pcb, stackup: Option<&Stackup>) -> String {
    let mut out = String::new();
    let version = if pcb.version == 0 {
        20_240_108
    } else {
        pcb.version
    };
    let generator = if pcb.generator.is_empty() {
        "kiclaude"
    } else {
        pcb.generator.as_str()
    };
    let thickness = if pcb.thickness_mm == 0.0 {
        1.6
    } else {
        pcb.thickness_mm
    };
    let paper = if pcb.paper.is_empty() {
        "A4"
    } else {
        pcb.paper.as_str()
    };

    writeln!(
        out,
        "(kicad_pcb (version {version}) (generator {generator})"
    )
    .expect("write to String");
    out.push('\n');

    // `(general (thickness X))`
    out.push_str("  (general\n");
    writeln!(out, "    (thickness {})", format_float(thickness)).expect("write");
    out.push_str("  )\n");
    out.push('\n');

    // `(paper "<format>")`
    writeln!(out, "  (paper {})", quote(paper)).expect("write");

    // `(layers …)`
    emit_layers(&pcb.layers, &mut out);
    out.push('\n');

    // `(setup …)`
    emit_setup(pcb, stackup, &mut out);
    out.push('\n');

    // `(net_class …)` blocks.
    if !pcb.net_classes.is_empty() {
        for nc in &pcb.net_classes {
            emit_net_class(nc, &mut out);
        }
        out.push('\n');
    }

    // Nets: emit the implicit `(net 0 "")` first, then named nets in order.
    out.push_str("  (net 0 \"\")\n");
    for (i, net) in pcb.nets.iter().enumerate() {
        writeln!(out, "  (net {} {})", i + 1, quote(&net.name)).expect("write");
    }
    if !pcb.footprints.is_empty()
        || !pcb.tracks.is_empty()
        || !pcb.vias.is_empty()
        || !pcb.zones.is_empty()
        || !pcb.drawings.is_empty()
        || !pcb.outline.points_mm.is_empty()
    {
        out.push('\n');
    }

    let net_id_of = build_net_id_table(&pcb.nets);

    for fp in &pcb.footprints {
        emit_footprint(fp, &net_id_of, &mut out);
        out.push('\n');
    }

    for t in &pcb.tracks {
        emit_track(t, &net_id_of, &mut out);
    }
    if !pcb.tracks.is_empty() {
        out.push('\n');
    }

    for v in &pcb.vias {
        emit_via(v, &net_id_of, &mut out);
    }
    if !pcb.vias.is_empty() {
        out.push('\n');
    }

    for z in &pcb.zones {
        emit_zone(z, &net_id_of, &mut out);
        out.push('\n');
    }

    emit_outline(&pcb.outline, &mut out);

    for d in &pcb.drawings {
        emit_drawing(d, &mut out);
    }

    out.push(')');
    out.push('\n');
    out
}

fn emit_layers(layers: &[Layer], out: &mut String) {
    out.push_str("  (layers\n");
    for layer in layers {
        write!(
            out,
            "    ({} {} {}",
            layer.id,
            quote(&layer.name),
            layer.kind
        )
        .expect("write");
        if !layer.purpose.is_empty() {
            out.push(' ');
            out.push_str(&quote(&layer.purpose));
        }
        out.push_str(")\n");
    }
    out.push_str("  )\n");
}

fn emit_setup(pcb: &Pcb, stackup: Option<&Stackup>, out: &mut String) {
    out.push_str("  (setup\n");
    if let Some(s) = stackup {
        emit_stackup(s, out);
    }
    writeln!(
        out,
        "    (pad_to_mask_clearance {})",
        format_float(pcb.pad_to_mask_clearance_mm)
    )
    .expect("write");
    if pcb.solder_mask_min_width_mm != 0.0 {
        writeln!(
            out,
            "    (solder_mask_min_width {})",
            format_float(pcb.solder_mask_min_width_mm)
        )
        .expect("write");
    }
    out.push_str("  )\n");
}

/// Emit the `(stackup …)` form inside `(setup …)`.
///
/// Shape mirrors `KiCad 9`'s stack manager output: one `(layer …)` per
/// physical layer in top-down order, then an optional
/// `(copper_finish "…")` trailing line. The `(type …)` tokens we write
/// are chosen so the case-insensitive matcher in
/// `super::pcb::stackup_kind_from_kicad` reads them back to the same
/// [`StackupLayerKind`] value — that's the round-trip contract M3-R-01
/// adds to the M0 byte-identity gate.
fn emit_stackup(stackup: &Stackup, out: &mut String) {
    out.push_str("    (stackup\n");
    for layer in &stackup.layers {
        emit_stackup_layer(layer, out);
    }
    if !stackup.finish.is_empty() {
        writeln!(out, "      (copper_finish {})", quote(&stackup.finish)).expect("write");
    }
    out.push_str("    )\n");
}

fn emit_stackup_layer(layer: &StackupLayer, out: &mut String) {
    write!(
        out,
        "      (layer {} (type {})",
        quote(&layer.name),
        quote(kicad_type_for_kind(layer.kind))
    )
    .expect("write");
    if layer.thickness_mm != 0.0 {
        write!(out, " (thickness {})", format_float(layer.thickness_mm)).expect("write");
    }
    // Dielectric layers carry the material name (re-emitted as
    // `(material "FR4")`) and electrical constants. Copper layers in
    // KiCad's stack manager don't carry epsilon_r / loss_tangent.
    if matches!(layer.kind, StackupLayerKind::Dielectric) && !layer.color.is_empty() {
        write!(out, " (material {})", quote(&layer.color)).expect("write");
    }
    if let Some(er) = layer.dielectric_constant {
        write!(out, " (epsilon_r {})", format_float(er)).expect("write");
    }
    if let Some(tan) = layer.loss_tangent {
        write!(out, " (loss_tangent {})", format_float(tan)).expect("write");
    }
    out.push_str(")\n");
}

fn kicad_type_for_kind(kind: StackupLayerKind) -> &'static str {
    // Choose canonical strings the parser's containment matcher reads
    // back to the same enum variant (see
    // `super::pcb::stackup_kind_from_kicad`).
    match kind {
        StackupLayerKind::Copper => "copper",
        StackupLayerKind::Dielectric => "core",
        StackupLayerKind::Soldermask => "solder_mask",
        StackupLayerKind::Silkscreen => "silkscreen",
        StackupLayerKind::Paste => "solder_paste",
        StackupLayerKind::Adhesive => "adhesive",
    }
}

fn emit_net_class(nc: &NetClass, out: &mut String) {
    write!(
        out,
        "  (net_class {} {}",
        quote(&nc.name),
        quote(&nc.description)
    )
    .expect("write");
    write!(out, "\n    (clearance {})", format_float(nc.clearance_mm)).expect("write");
    write!(
        out,
        "\n    (trace_width {})",
        format_float(nc.trace_width_mm)
    )
    .expect("write");
    write!(out, "\n    (via_dia {})", format_float(nc.via_diameter_mm)).expect("write");
    write!(out, "\n    (via_drill {})", format_float(nc.via_drill_mm)).expect("write");
    if let Some(w) = nc.diff_pair_width_mm {
        write!(out, "\n    (diff_pair_width {})", format_float(w)).expect("write");
    }
    if let Some(g) = nc.diff_pair_gap_mm {
        write!(out, "\n    (diff_pair_gap {})", format_float(g)).expect("write");
    }
    out.push_str("\n  )\n");
}

fn emit_footprint(fp: &FootprintInstance, net_id_of: &BTreeMap<String, u32>, out: &mut String) {
    writeln!(out, "  (footprint {}", quote(&fp.lib_id)).expect("write");
    writeln!(out, "    (layer {})", quote(&fp.layer.0)).expect("write");
    if !fp.uuid.is_empty() {
        writeln!(out, "    (uuid {})", quote(&fp.uuid)).expect("write");
    }
    writeln!(
        out,
        "    (at {} {} {})",
        format_float(fp.position_mm.0),
        format_float(fp.position_mm.1),
        format_float(fp.rotation_deg),
    )
    .expect("write");

    // Merge the `locked` bool into the attributes vec for emission. The
    // KCIR snapshot keeps both for ergonomic access, but the on-disk
    // form uses a single `(attr …)` list.
    let mut attrs: Vec<String> = fp.attributes.clone();
    if fp.locked && !attrs.iter().any(|a| a == "locked") {
        attrs.push("locked".to_string());
    }
    if !attrs.is_empty() {
        writeln!(out, "    (attr {})", attrs.join(" ")).expect("write");
    }

    if !fp.refdes.is_empty() {
        writeln!(out, "    (property \"Reference\" {})", quote(&fp.refdes)).expect("write");
    }
    if !fp.value.is_empty() {
        writeln!(out, "    (property \"Value\" {})", quote(&fp.value)).expect("write");
    }
    if !fp.mpn.is_empty() {
        writeln!(out, "    (property \"MPN\" {})", quote(&fp.mpn)).expect("write");
    }

    for pad in &fp.pads {
        emit_pad(pad, net_id_of, out);
    }

    for d in &fp.drawings {
        emit_inner_drawing(d, out);
    }

    if let Some(c) = &fp.courtyard {
        emit_courtyard(c, out);
    }

    for m in &fp.models_3d {
        emit_model_3d(m, out);
    }

    out.push_str("  )\n");
}

fn emit_pad(pad: &Pad, net_id_of: &BTreeMap<String, u32>, out: &mut String) {
    let pad_type = if pad.pad_type.is_empty() {
        "smd"
    } else {
        pad.pad_type.as_str()
    };
    let shape = if pad.shape.is_empty() {
        "circle"
    } else {
        pad.shape.as_str()
    };
    write!(out, "    (pad {} {pad_type} {shape}", quote(&pad.number)).expect("write");
    write!(
        out,
        " (at {} {}",
        format_float(pad.position_mm.0),
        format_float(pad.position_mm.1),
    )
    .expect("write");
    if pad.rotation_deg != 0.0 {
        write!(out, " {}", format_float(pad.rotation_deg)).expect("write");
    }
    out.push(')');
    write!(
        out,
        " (size {} {})",
        format_float(pad.size_mm.0),
        format_float(pad.size_mm.1),
    )
    .expect("write");
    if let Some((dw, dh)) = pad.drill_mm {
        // KiCad uses the `oval` keyword to mark a stadium drill. Two
        // bitwise-identical f64s round-trip through parse → emit, so a
        // literal `==` is the right test here (suppressing the clippy
        // float_cmp lint for this specific equality).
        #[allow(clippy::float_cmp)]
        let is_round = dw == dh;
        if is_round {
            write!(out, " (drill {})", format_float(dw)).expect("write");
        } else {
            write!(
                out,
                " (drill oval {} {})",
                format_float(dw),
                format_float(dh),
            )
            .expect("write");
        }
    }
    out.push_str(" (layers");
    for l in &pad.layers {
        write!(out, " {}", quote(&l.0)).expect("write");
    }
    out.push(')');
    if let Some(r) = pad.roundrect_rratio {
        write!(out, " (roundrect_rratio {})", format_float(r)).expect("write");
    }
    if !pad.net.is_empty() {
        let net_id = net_id_of.get(&pad.net).copied().unwrap_or(0);
        write!(out, " (net {net_id} {})", quote(&pad.net)).expect("write");
    }
    if !pad.uuid.is_empty() {
        write!(out, " (uuid {})", quote(&pad.uuid)).expect("write");
    }
    out.push_str(")\n");
}

fn emit_inner_drawing(d: &Drawing, out: &mut String) {
    let head = if d.kind.is_empty() {
        "fp_line"
    } else {
        d.kind.as_str()
    };
    write!(out, "    ({head}").expect("write");
    match (head, d.points_mm.as_slice()) {
        ("fp_text" | "gr_text", _) => {
            out.push(' ');
            out.push_str(&quote(&d.text));
            if let Some(p) = d.points_mm.first() {
                write!(out, " (at {} {})", format_float(p.0), format_float(p.1)).expect("write");
            }
        }
        ("fp_line" | "fp_arc" | "fp_rect" | "gr_line" | "gr_arc" | "gr_rect", [start, end, ..]) => {
            write!(
                out,
                " (start {} {}) (end {} {})",
                format_float(start.0),
                format_float(start.1),
                format_float(end.0),
                format_float(end.1),
            )
            .expect("write");
        }
        ("fp_circle" | "gr_circle", [center, edge, ..]) => {
            write!(
                out,
                " (center {} {}) (end {} {})",
                format_float(center.0),
                format_float(center.1),
                format_float(edge.0),
                format_float(edge.1),
            )
            .expect("write");
        }
        _ => {}
    }
    writeln!(
        out,
        " (stroke (width {}) (type default)) (layer {}) (uuid {}))",
        format_float(if d.width_mm == 0.0 { 0.1 } else { d.width_mm }),
        quote(&d.layer.0),
        quote(&d.uuid),
    )
    .expect("write");
}

fn emit_courtyard(c: &FootprintCourtyard, out: &mut String) {
    out.push_str("    (fp_poly (pts");
    for (x, y) in &c.points_mm {
        write!(out, " (xy {} {})", format_float(*x), format_float(*y)).expect("write");
    }
    let width = if c.width_mm == 0.0 { 0.05 } else { c.width_mm };
    writeln!(
        out,
        ") (stroke (width {}) (type default)) (fill none) (layer {}) (uuid \"\"))",
        format_float(width),
        quote(&c.layer.0),
    )
    .expect("write");
}

fn emit_model_3d(m: &Model3D, out: &mut String) {
    writeln!(out, "    (model {}", quote(&m.path)).expect("write");
    writeln!(
        out,
        "      (offset (xyz {} {} {}))",
        format_float(m.offset_mm.0),
        format_float(m.offset_mm.1),
        format_float(m.offset_mm.2),
    )
    .expect("write");
    writeln!(
        out,
        "      (scale (xyz {} {} {}))",
        format_float(m.scale.0),
        format_float(m.scale.1),
        format_float(m.scale.2),
    )
    .expect("write");
    writeln!(
        out,
        "      (rotate (xyz {} {} {}))",
        format_float(m.rotate_deg.0),
        format_float(m.rotate_deg.1),
        format_float(m.rotate_deg.2),
    )
    .expect("write");
    out.push_str("    )\n");
}

fn emit_track(track: &Track, net_id_of: &BTreeMap<String, u32>, out: &mut String) {
    let net_id = net_id_of.get(&track.net).copied().unwrap_or(0);
    let start = track.points_mm.first().copied().unwrap_or((0.0, 0.0));
    let end = track.points_mm.last().copied().unwrap_or(start);
    write!(
        out,
        "  (segment (start {} {}) (end {} {}) (width {}) (layer {}) (net {})",
        format_float(start.0),
        format_float(start.1),
        format_float(end.0),
        format_float(end.1),
        format_float(track.width_mm),
        quote(&track.layer.0),
        net_id,
    )
    .expect("write");
    if track.locked {
        out.push_str(" (locked)");
    }
    writeln!(out, " (uuid {}))", quote(&track.uuid)).expect("write");
}

fn emit_via(via: &Via, net_id_of: &BTreeMap<String, u32>, out: &mut String) {
    let net_id = net_id_of.get(&via.net).copied().unwrap_or(0);
    out.push_str("  (via");
    match via.kind.as_str() {
        "blind" | "buried" => {
            out.push(' ');
            out.push_str(&via.kind);
        }
        _ => {}
    }
    write!(
        out,
        " (at {} {}) (size {}) (drill {}) (layers {} {}) (net {})",
        format_float(via.position_mm.0),
        format_float(via.position_mm.1),
        format_float(via.diameter_mm),
        format_float(via.drill_mm),
        quote(&via.from_layer.0),
        quote(&via.to_layer.0),
        net_id,
    )
    .expect("write");
    if via.locked {
        out.push_str(" (locked)");
    }
    writeln!(out, " (uuid {}))", quote(&via.uuid)).expect("write");
}

fn emit_zone(zone: &Zone, net_id_of: &BTreeMap<String, u32>, out: &mut String) {
    let net_id = net_id_of.get(&zone.net).copied().unwrap_or(0);
    writeln!(
        out,
        "  (zone (net {}) (net_name {}) (layer {}) (uuid {})",
        net_id,
        quote(&zone.net),
        quote(&zone.layer.0),
        quote(&zone.uuid),
    )
    .expect("write");
    if zone.hatched {
        out.push_str("    (hatched)\n");
    }
    // `(connect_pads MODE (clearance X))`. MODE is omitted when the
    // KCIR connect_pads is "yes" AND thermal_relief is false; otherwise
    // we emit `thermal_reliefs` if thermal_relief is true (which is
    // KiCad's canonical keyword), or the connect_pads string verbatim.
    let mode = if zone.connect_pads != "yes" && !zone.connect_pads.is_empty() {
        zone.connect_pads.as_str()
    } else if zone.thermal_relief {
        "thermal_reliefs"
    } else {
        ""
    };
    if mode.is_empty() {
        writeln!(
            out,
            "    (connect_pads (clearance {}))",
            format_float(zone.clearance_mm)
        )
        .expect("write");
    } else {
        writeln!(
            out,
            "    (connect_pads {mode} (clearance {}))",
            format_float(zone.clearance_mm)
        )
        .expect("write");
    }
    writeln!(
        out,
        "    (min_thickness {})",
        format_float(zone.min_thickness_mm)
    )
    .expect("write");
    if zone.thermal_relief && zone.thermal_gap_mm != 0.0 {
        writeln!(
            out,
            "    (thermal_gap {})",
            format_float(zone.thermal_gap_mm)
        )
        .expect("write");
    }
    if zone.thermal_relief && zone.thermal_bridge_width_mm != 0.0 {
        writeln!(
            out,
            "    (thermal_bridge_width {})",
            format_float(zone.thermal_bridge_width_mm)
        )
        .expect("write");
    }
    if !zone.outline_mm.is_empty() {
        out.push_str("    (polygon (pts");
        for (x, y) in &zone.outline_mm {
            write!(out, " (xy {} {})", format_float(*x), format_float(*y)).expect("write");
        }
        out.push_str("))\n");
    }
    for cutout in &zone.cutouts_mm {
        if cutout.is_empty() {
            continue;
        }
        out.push_str("    (polygon (pts");
        for (x, y) in cutout {
            write!(out, " (xy {} {})", format_float(*x), format_float(*y)).expect("write");
        }
        out.push_str("))\n");
    }
    for fill in &zone.filled_polygons {
        if fill.is_empty() {
            continue;
        }
        write!(
            out,
            "    (filled_polygon (layer {}) (pts",
            quote(&zone.layer.0)
        )
        .expect("write");
        for (x, y) in fill {
            write!(out, " (xy {} {})", format_float(*x), format_float(*y)).expect("write");
        }
        out.push_str("))\n");
    }
    out.push_str("  )\n");
}

fn emit_outline(outline: &Outline, out: &mut String) {
    let pts = &outline.points_mm;
    let mut i = 0;
    while i + 1 < pts.len() {
        let (sx, sy) = pts[i];
        let (ex, ey) = pts[i + 1];
        writeln!(
            out,
            "  (gr_line (start {} {}) (end {} {}) (stroke (width 0.05) (type default)) (layer \"Edge.Cuts\") (uuid \"\"))",
            format_float(sx),
            format_float(sy),
            format_float(ex),
            format_float(ey),
        )
        .expect("write");
        i += 2;
    }
}

fn emit_drawing(d: &Drawing, out: &mut String) {
    if d.kind == "gr_line" && d.layer.0 == "Edge.Cuts" {
        return;
    }
    let head = if d.kind.is_empty() {
        "gr_line"
    } else {
        d.kind.as_str()
    };
    write!(out, "  ({head}").expect("write");
    match (head, d.points_mm.as_slice()) {
        ("gr_text", _) => {
            out.push(' ');
            out.push_str(&quote(&d.text));
            if let Some(p) = d.points_mm.first() {
                write!(out, " (at {} {})", format_float(p.0), format_float(p.1)).expect("write");
            }
        }
        ("gr_line" | "gr_arc" | "gr_rect", [start, end, ..]) => {
            write!(
                out,
                " (start {} {}) (end {} {})",
                format_float(start.0),
                format_float(start.1),
                format_float(end.0),
                format_float(end.1),
            )
            .expect("write");
        }
        ("gr_circle", [center, edge, ..]) => {
            write!(
                out,
                " (center {} {}) (end {} {})",
                format_float(center.0),
                format_float(center.1),
                format_float(edge.0),
                format_float(edge.1),
            )
            .expect("write");
        }
        _ => {}
    }
    writeln!(
        out,
        " (stroke (width {}) (type default)) (layer {}) (uuid {}))",
        format_float(if d.width_mm == 0.0 { 0.1 } else { d.width_mm }),
        quote(&d.layer.0),
        quote(&d.uuid),
    )
    .expect("write");
}

/// Build a `net name → 1-based id` table matching the order of
/// [`emit_pcb`]'s `(net N "name")` emission.
fn build_net_id_table(nets: &[Net]) -> BTreeMap<String, u32> {
    let mut by_name: BTreeMap<String, u32> = BTreeMap::new();
    for (i, n) in nets.iter().enumerate() {
        if !n.name.is_empty() {
            #[allow(clippy::cast_possible_truncation)]
            by_name.insert(n.name.clone(), (i + 1) as u32);
        }
    }
    by_name
}

/// Format an f64 in canonical form: shortest unambiguous decimal with at
/// least one digit after the point (`100` → `100.0`, `0.25` → `0.25`).
fn format_float(v: f64) -> String {
    let s = format!("{v}");
    if s.contains('.') || s.contains('e') || s.contains("inf") || s.contains("NaN") {
        s
    } else {
        format!("{s}.0")
    }
}

/// Wrap a string in double quotes and escape embedded `\` / `"`.
fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            other => out.push(other),
        }
    }
    out.push('"');
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kcir::{
        FootprintInstance, Layer, LayerRef, Net, NetClass, Pad, Pcb, Track, Via, Zone,
    };
    use pretty_assertions::assert_eq;

    fn sample_pcb() -> Pcb {
        Pcb {
            version: 20_240_108,
            generator: "kiclaude".to_string(),
            thickness_mm: 1.6,
            paper: "A4".to_string(),
            layers: vec![
                Layer {
                    id: 0,
                    name: "F.Cu".to_string(),
                    kind: "signal".to_string(),
                    purpose: String::new(),
                },
                Layer {
                    id: 31,
                    name: "B.Cu".to_string(),
                    kind: "signal".to_string(),
                    purpose: String::new(),
                },
                Layer {
                    id: 44,
                    name: "Edge.Cuts".to_string(),
                    kind: "user".to_string(),
                    purpose: String::new(),
                },
            ],
            nets: vec![Net {
                name: "VCC".to_string(),
                ..Net::default()
            }],
            footprints: vec![FootprintInstance {
                uuid: "22222222-2222-2222-2222-222222222222".to_string(),
                refdes: "R1".to_string(),
                lib_id: "Resistor_SMD:R_0603_1608Metric".to_string(),
                value: "10k".to_string(),
                layer: LayerRef("F.Cu".to_string()),
                position_mm: (100.0, 50.0),
                rotation_deg: 90.0,
                ..FootprintInstance::default()
            }],
            tracks: vec![Track {
                uuid: "33333333-3333-3333-3333-333333333333".to_string(),
                layer: LayerRef("F.Cu".to_string()),
                net: "VCC".to_string(),
                points_mm: vec![(100.0, 50.0), (110.0, 50.0)],
                width_mm: 0.25,
                ..Track::default()
            }],
            zones: vec![Zone {
                uuid: "44444444-4444-4444-4444-444444444444".to_string(),
                layer: LayerRef("F.Cu".to_string()),
                net: "VCC".to_string(),
                outline_mm: vec![(90.0, 40.0), (120.0, 40.0), (120.0, 60.0), (90.0, 60.0)],
                thermal_relief: true,
                ..Zone::default()
            }],
            ..Pcb::default()
        }
    }

    /// Smoke: [`emit_pcb`] is deterministic — same Pcb, same string.
    #[test]
    fn smoke_emit_is_deterministic() {
        let pcb = sample_pcb();
        assert_eq!(emit_pcb(&pcb), emit_pcb(&pcb));
    }

    /// Smoke: emitted text parses back through the s-expr lexer.
    #[test]
    fn smoke_emit_re_parses() {
        let pcb = sample_pcb();
        let text = emit_pcb(&pcb);
        let parsed = crate::sexpr::parse_str(&text).expect("parses");
        assert_eq!(parsed.len(), 1);
        assert_eq!(parsed[0].head_symbol(), Some("kicad_pcb"));
    }

    /// Smoke: [`format_float`] always emits a decimal point.
    #[test]
    fn smoke_format_float_always_has_decimal_point() {
        assert_eq!(format_float(100.0), "100.0");
        assert_eq!(format_float(0.25), "0.25");
        assert_eq!(format_float(-1.5), "-1.5");
    }

    /// Smoke: [`quote`] escapes embedded `"` and `\`.
    #[test]
    fn smoke_quote_escapes() {
        assert_eq!(quote("hello"), "\"hello\"");
        assert_eq!(quote("a\"b"), "\"a\\\"b\"");
        assert_eq!(quote("c\\d"), "\"c\\\\d\"");
    }

    /// A `net_class` with diff-pair fields round-trips through emit. The
    /// parser side is verified by the integration tests in `pcb.rs`.
    #[test]
    fn emits_net_class_with_diff_pair() {
        let pcb = Pcb {
            net_classes: vec![NetClass {
                name: "DiffSig".to_string(),
                description: "USB / LVDS".to_string(),
                clearance_mm: 0.2,
                trace_width_mm: 0.2,
                via_drill_mm: 0.3,
                via_diameter_mm: 0.6,
                diff_pair_width_mm: Some(0.18),
                diff_pair_gap_mm: Some(0.12),
            }],
            ..Pcb::default()
        };
        let text = emit_pcb(&pcb);
        assert!(text.contains("(net_class \"DiffSig\" \"USB / LVDS\""));
        assert!(text.contains("(diff_pair_width 0.18)"));
        assert!(text.contains("(diff_pair_gap 0.12)"));
    }

    /// A pad with a circular drill emits `(drill D)` and a roundrect
    /// rratio survives the round-trip.
    #[test]
    fn emits_pad_with_drill_and_rratio() {
        let pad = Pad {
            number: "1".to_string(),
            pad_type: "thru_hole".to_string(),
            shape: "roundrect".to_string(),
            position_mm: (1.27, 0.0),
            rotation_deg: 0.0,
            size_mm: (1.5, 1.5),
            drill_mm: Some((0.8, 0.8)),
            layers: vec![LayerRef("*.Cu".to_string()), LayerRef("*.Mask".to_string())],
            net: String::new(),
            roundrect_rratio: Some(0.25),
            uuid: "p-1".to_string(),
        };
        let net_table = BTreeMap::new();
        let mut out = String::new();
        emit_pad(&pad, &net_table, &mut out);
        assert!(out.contains("(drill 0.8)"));
        assert!(out.contains("(layers \"*.Cu\" \"*.Mask\")"));
        assert!(out.contains("(roundrect_rratio 0.25)"));
    }

    /// Oval drill emits `(drill oval W H)`.
    #[test]
    fn emits_oval_drill() {
        let pad = Pad {
            number: "1".to_string(),
            pad_type: "thru_hole".to_string(),
            shape: "oval".to_string(),
            position_mm: (0.0, 0.0),
            size_mm: (2.0, 1.5),
            drill_mm: Some((1.2, 0.8)),
            layers: vec![LayerRef("*.Cu".to_string())],
            ..Pad::default()
        };
        let net_table = BTreeMap::new();
        let mut out = String::new();
        emit_pad(&pad, &net_table, &mut out);
        assert!(out.contains("(drill oval 1.2 0.8)"));
    }

    /// A locked track gets `(locked)` between net and uuid.
    #[test]
    fn emits_locked_track() {
        let track = Track {
            uuid: "t1".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "VCC".to_string(),
            points_mm: vec![(0.0, 0.0), (1.0, 0.0)],
            width_mm: 0.25,
            locked: true,
        };
        let mut net_table = BTreeMap::new();
        net_table.insert("VCC".to_string(), 1);
        let mut out = String::new();
        emit_track(&track, &net_table, &mut out);
        assert!(out.contains("(locked) (uuid"));
    }

    /// A blind via emits the `blind` keyword right after `(via`.
    #[test]
    fn emits_blind_via() {
        let via = Via {
            uuid: "v1".to_string(),
            net: String::new(),
            position_mm: (5.0, 5.0),
            from_layer: LayerRef("F.Cu".to_string()),
            to_layer: LayerRef("In1.Cu".to_string()),
            drill_mm: 0.3,
            diameter_mm: 0.6,
            kind: "blind".to_string(),
            locked: false,
        };
        let net_table = BTreeMap::new();
        let mut out = String::new();
        emit_via(&via, &net_table, &mut out);
        assert!(out.starts_with("  (via blind "));
    }

    /// A zone with cutouts emits one polygon per cutout after the outline.
    #[test]
    fn emits_zone_with_cutouts() {
        let zone = Zone {
            uuid: "z1".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "GND".to_string(),
            outline_mm: vec![(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)],
            cutouts_mm: vec![vec![(2.0, 2.0), (4.0, 2.0), (4.0, 4.0), (2.0, 4.0)]],
            min_thickness_mm: 0.25,
            ..Zone::default()
        };
        let mut net_table = BTreeMap::new();
        net_table.insert("GND".to_string(), 1);
        let mut out = String::new();
        emit_zone(&zone, &net_table, &mut out);
        let polygon_count = out.matches("(polygon (pts").count();
        assert_eq!(polygon_count, 2, "outline + one cutout = 2 polygons");
    }

    /// Hatched zone gets a `(hatched)` line.
    #[test]
    fn emits_hatched_zone() {
        let zone = Zone {
            uuid: "z1".to_string(),
            layer: LayerRef("F.Cu".to_string()),
            net: "GND".to_string(),
            hatched: true,
            outline_mm: vec![(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)],
            ..Zone::default()
        };
        let mut net_table = BTreeMap::new();
        net_table.insert("GND".to_string(), 1);
        let mut out = String::new();
        emit_zone(&zone, &net_table, &mut out);
        assert!(out.contains("    (hatched)\n"));
    }
}
