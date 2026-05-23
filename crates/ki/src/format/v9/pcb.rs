//! `.kicad_pcb` S-expression → KCIR [`Pcb`].
//!
//! The mapper walks a parsed [`SNode`] tree (head must be `kicad_pcb`)
//! and lifts the forms KCIR cares about: layers, net classes, nets,
//! footprints (with pads, courtyards, 3D models, inner drawings),
//! tracks, vias, zones, board outline, drawings.
//!
//! Forms we don't yet model are silently ignored — the parsed tree is
//! the canonical store, KCIR is the editing view.
//!
//! Numbers in `KiCad` S-exprs are bareword symbols, so we parse them
//! out of the symbol text here (via `atom_f64` / `atom_i32`).
//!
//! # M2-R-01 scope
//!
//! Reads every shape required for byte-identical round-trip against the
//! canonical emitter in [`super::emit`]. The companion parse-only walk
//! over `development/resources/kicad/...` lives in
//! `tests/golden/round_trip_pcb.rs` and asserts that no real-world
//! KiCad-9 board panics this parser.

use std::collections::HashMap;

use super::sexpr_helpers::{
    atom_f64, atom_i32, atom_str, body_children, collect_xy_points, find_child, find_children,
};
use crate::kcir::{
    Drawing, FootprintCourtyard, FootprintInstance, Layer, LayerRef, Model3D, Net, NetClass,
    NetClassRef, Outline, Pad, Pcb, Track, Via, Zone,
};
use crate::sexpr::SNode;

/// Map a parsed `(kicad_pcb …)` form to a KCIR [`Pcb`].
///
/// Returns a human-readable error string on the rare cases where the
/// tree is structurally wrong (e.g. a `(net N "name")` without an id).
/// Most malformed sub-forms are skipped with the mapper continuing.
///
/// # Errors
/// Returns `Err(String)` when the root is not a `(kicad_pcb …)` form.
pub fn map_pcb(root: &SNode) -> Result<Pcb, String> {
    if root.head_symbol() != Some("kicad_pcb") {
        return Err(format!(
            "expected (kicad_pcb …) root, got {:?}",
            root.head_symbol()
        ));
    }
    let mut pcb = Pcb::default();
    map_header(root, &mut pcb);
    map_layers(root, &mut pcb);
    map_setup(root, &mut pcb);
    map_net_classes(root, &mut pcb);
    let net_names = map_nets(root, &mut pcb);
    map_footprints(root, &mut pcb, &net_names);
    map_tracks(root, &mut pcb, &net_names);
    map_vias(root, &mut pcb, &net_names);
    map_zones(root, &mut pcb, &net_names);
    map_outline(root, &mut pcb);
    map_drawings(root, &mut pcb);
    Ok(pcb)
}

/// `(version N)`, `(generator <name>)`, `(general (thickness X))`,
/// `(paper "<format>")`.
fn map_header(root: &SNode, pcb: &mut Pcb) {
    if let Some(form) = find_child(root, "version") {
        if let Some(n) = body_children(form).next() {
            if let Some(v) = atom_str(n).and_then(|s| s.parse::<u32>().ok()) {
                pcb.version = v;
            }
        }
    }
    if let Some(form) = find_child(root, "generator") {
        if let Some(n) = body_children(form).next() {
            pcb.generator = atom_str(n).unwrap_or("").to_string();
        }
    }
    if let Some(general) = find_child(root, "general") {
        if let Some(thickness) = find_child(general, "thickness") {
            pcb.thickness_mm = body_children(thickness)
                .next()
                .and_then(atom_f64)
                .unwrap_or(0.0);
        }
    }
    if let Some(form) = find_child(root, "paper") {
        if let Some(n) = body_children(form).next() {
            pcb.paper = atom_str(n).unwrap_or("").to_string();
        }
    }
}

/// `(setup (pad_to_mask_clearance X) (solder_mask_min_width X) …)`.
fn map_setup(root: &SNode, pcb: &mut Pcb) {
    let Some(setup) = find_child(root, "setup") else {
        return;
    };
    if let Some(p) = find_child(setup, "pad_to_mask_clearance") {
        pcb.pad_to_mask_clearance_mm = body_children(p).next().and_then(atom_f64).unwrap_or(0.0);
    }
    if let Some(p) = find_child(setup, "solder_mask_min_width") {
        pcb.solder_mask_min_width_mm = body_children(p).next().and_then(atom_f64).unwrap_or(0.0);
    }
}

/// `(layers (0 "F.Cu" signal) (31 "B.Cu" signal) (32 "B.Adhes" user "B.Adhesive") …)`
fn map_layers(root: &SNode, pcb: &mut Pcb) {
    let Some(layers) = find_child(root, "layers") else {
        return;
    };
    for entry in body_children(layers) {
        let SNode::List { children, .. } = entry else {
            continue;
        };
        if children.is_empty() {
            continue;
        }
        // Layer rows look like (<id> "<name>" <kind> [<display name>]). The
        // numeric id IS the head — there's no leading symbol head.
        let id = atom_i32(&children[0]).unwrap_or(0);
        let name = children.get(1).and_then(atom_str).unwrap_or("").to_string();
        let kind = children.get(2).and_then(atom_str).unwrap_or("").to_string();
        let purpose = children.get(3).and_then(atom_str).unwrap_or("").to_string();
        pcb.layers.push(Layer {
            id,
            name,
            kind,
            purpose,
        });
    }
}

/// Top-level `(net_class "NAME" "DESC" (clearance X) (trace_width X)
/// (via_dia X) (via_drill X) [(diff_pair_width X)] [(diff_pair_gap X)])`.
fn map_net_classes(root: &SNode, pcb: &mut Pcb) {
    for form in find_children(root, "net_class") {
        let body: Vec<&SNode> = body_children(form).collect();
        let name = body.first().and_then(|n| atom_str(n)).unwrap_or("");
        let description = body.get(1).and_then(|n| atom_str(n)).unwrap_or("");
        let clearance = find_child(form, "clearance")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let trace = find_child(form, "trace_width")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let via_dia = find_child(form, "via_dia")
            .or_else(|| find_child(form, "via_diameter"))
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let via_drill = find_child(form, "via_drill")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let diff_pair_width = find_child(form, "diff_pair_width")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64);
        let diff_pair_gap = find_child(form, "diff_pair_gap")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64);
        pcb.net_classes.push(NetClass {
            name: name.to_string(),
            description: description.to_string(),
            clearance_mm: clearance,
            trace_width_mm: trace,
            via_drill_mm: via_drill,
            via_diameter_mm: via_dia,
            diff_pair_width_mm: diff_pair_width,
            diff_pair_gap_mm: diff_pair_gap,
        });
    }
}

/// `(net <id> "<name>")` — returns a map from numeric net id to net name
/// so segment/via/zone mappers can resolve their `(net N)` references.
fn map_nets(root: &SNode, pcb: &mut Pcb) -> HashMap<i32, String> {
    let mut by_id: HashMap<i32, String> = HashMap::new();
    for form in find_children(root, "net") {
        let body: Vec<&SNode> = body_children(form).collect();
        if body.len() < 2 {
            continue;
        }
        let Some(id) = atom_i32(body[0]) else {
            continue;
        };
        let name = atom_str(body[1]).unwrap_or("").to_string();
        by_id.insert(id, name.clone());
        // Skip the implicit "no-net" 0 entry — KCIR `Net` is for named
        // electrical nets, not the placeholder.
        if id != 0 {
            pcb.nets.push(Net {
                name,
                class: NetClassRef::default(),
                ..Net::default()
            });
        }
    }
    by_id
}

/// `(footprint "<lib_id>" (layer …) (uuid …) (at x y [rot]) (attr …)?
///  (property …)* (pad …)* (fp_line …)* (fp_poly …)* (model …)*)`
fn map_footprints(root: &SNode, pcb: &mut Pcb, net_names: &HashMap<i32, String>) {
    for form in find_children(root, "footprint") {
        let body: Vec<&SNode> = body_children(form).collect();
        let lib_id = body
            .first()
            .and_then(|n| atom_str(n))
            .unwrap_or("")
            .to_string();

        let layer = find_child(form, "layer")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("F.Cu")
            .to_string();

        let uuid = find_child(form, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();

        let (x, y, rotation) = read_at(form);

        let mut refdes = String::new();
        let mut value = String::new();
        let mut mpn = String::new();
        for prop in find_children(form, "property") {
            let body: Vec<&SNode> = body_children(prop).collect();
            if body.len() < 2 {
                continue;
            }
            let key = atom_str(body[0]).unwrap_or("");
            let val = atom_str(body[1]).unwrap_or("").to_string();
            match key {
                "Reference" => refdes = val,
                "Value" => value = val,
                "MPN" | "Manufacturer Part Number" => mpn = val,
                _ => {}
            }
        }

        let attributes: Vec<String> = find_child(form, "attr")
            .map(|n| {
                body_children(n)
                    .filter_map(|c| atom_str(c).map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        let locked =
            find_child(form, "locked").is_some() || attributes.iter().any(|a| a == "locked");

        let pads = map_pads(form, net_names);
        let (drawings, courtyard) = map_footprint_drawings(form);
        let models_3d = map_models(form);

        pcb.footprints.push(FootprintInstance {
            uuid,
            refdes,
            lib_id,
            value,
            mpn,
            layer: LayerRef(layer),
            position_mm: (x, y),
            rotation_deg: rotation,
            locked,
            attributes,
            pads,
            courtyard,
            models_3d,
            drawings,
        });
    }
}

/// `(pad "1" smd roundrect (at x y [rot]) (size W H) [(drill D)|(drill oval W H)]
///   (layers "F.Cu" "F.Mask") [(roundrect_rratio R)] [(net N "name")] (uuid …))`
fn map_pads(footprint: &SNode, net_names: &HashMap<i32, String>) -> Vec<Pad> {
    let mut out = Vec::new();
    for form in find_children(footprint, "pad") {
        let body: Vec<&SNode> = body_children(form).collect();
        let number = body
            .first()
            .and_then(|n| atom_str(n))
            .unwrap_or("")
            .to_string();
        let pad_type = body
            .get(1)
            .and_then(|n| atom_str(n))
            .unwrap_or("")
            .to_string();
        let shape = body
            .get(2)
            .and_then(|n| atom_str(n))
            .unwrap_or("")
            .to_string();
        let (px, py, prot) = read_at(form);
        let size = find_child(form, "size").map_or((0.0, 0.0), read_point);
        let drill = find_child(form, "drill").and_then(read_drill);
        let layers: Vec<LayerRef> = find_child(form, "layers")
            .map(|n| {
                body_children(n)
                    .filter_map(|c| atom_str(c).map(|s| LayerRef(s.to_string())))
                    .collect()
            })
            .unwrap_or_default();
        let roundrect_rratio = find_child(form, "roundrect_rratio")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64);
        let net = read_pad_net(form, net_names);
        let uuid = find_child(form, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        out.push(Pad {
            number,
            pad_type,
            shape,
            position_mm: (px, py),
            rotation_deg: prot,
            size_mm: size,
            drill_mm: drill,
            layers,
            net,
            roundrect_rratio,
            uuid,
        });
    }
    out
}

/// `(drill D)` → `(D, D)`; `(drill oval W H)` → `(W, H)`.
fn read_drill(form: &SNode) -> Option<(f64, f64)> {
    let body: Vec<&SNode> = body_children(form).collect();
    let first = body.first()?;
    if let Some(s) = atom_str(first) {
        if s == "oval" {
            let w = body.get(1).and_then(|n| atom_f64(n))?;
            let h = body.get(2).and_then(|n| atom_f64(n))?;
            return Some((w, h));
        }
        if let Ok(d) = s.parse::<f64>() {
            return Some((d, d));
        }
    }
    None
}

/// `(net N "name")` inside a pad → the resolved name.
fn read_pad_net(pad: &SNode, net_names: &HashMap<i32, String>) -> String {
    let Some(net_form) = find_child(pad, "net") else {
        return String::new();
    };
    let body: Vec<&SNode> = body_children(net_form).collect();
    if let Some(first) = body.first() {
        if let Some(id) = atom_i32(first) {
            // Prefer the name in the second arg (KiCad emits both); fall
            // back to the global net-id table for `(net N)` shorthand.
            if let Some(second) = body.get(1).and_then(|n| atom_str(n)) {
                return second.to_string();
            }
            return net_names.get(&id).cloned().unwrap_or_default();
        }
        if let Some(s) = atom_str(first) {
            return s.to_string();
        }
    }
    String::new()
}

/// Read `fp_line` / `fp_arc` / `fp_rect` / `fp_circle` / `fp_text` /
/// `fp_poly` blocks inside a footprint, preserving declaration order.
/// `fp_poly` on `*.CrtYd` becomes the courtyard polygon; everything
/// else becomes a [`Drawing`].
fn map_footprint_drawings(form: &SNode) -> (Vec<Drawing>, Option<FootprintCourtyard>) {
    let kinds = [
        "fp_line",
        "fp_arc",
        "fp_rect",
        "fp_circle",
        "fp_text",
        "fp_poly",
    ];
    let mut drawings = Vec::new();
    let mut courtyard: Option<FootprintCourtyard> = None;
    let SNode::List { children, .. } = form else {
        return (drawings, courtyard);
    };

    for d in children {
        let kind = match d.head_symbol() {
            Some(k) if kinds.contains(&k) => k,
            _ => continue,
        };
        let layer = find_child(d, "layer")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        let width = find_child(d, "stroke")
            .and_then(|s| find_child(s, "width"))
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .or_else(|| {
                find_child(d, "width")
                    .and_then(|n| body_children(n).next())
                    .and_then(atom_f64)
            })
            .unwrap_or(0.0);
        let uuid = find_child(d, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();

        if kind == "fp_poly" {
            let points = find_child(d, "pts")
                .map(collect_xy_points)
                .unwrap_or_default();
            if (layer == "F.CrtYd" || layer == "B.CrtYd") && courtyard.is_none() {
                courtyard = Some(FootprintCourtyard {
                    layer: LayerRef(layer),
                    points_mm: points,
                    width_mm: width,
                });
            } else {
                drawings.push(Drawing {
                    uuid,
                    layer: LayerRef(layer),
                    kind: "fp_poly".to_string(),
                    points_mm: points,
                    width_mm: width,
                    text: String::new(),
                });
            }
            continue;
        }

        let points = read_drawing_points(d, kind);
        let text = if kind == "fp_text" {
            body_children(d)
                .find_map(atom_str)
                .unwrap_or("")
                .to_string()
        } else {
            String::new()
        };
        drawings.push(Drawing {
            uuid,
            layer: LayerRef(layer),
            kind: kind.to_string(),
            points_mm: points,
            width_mm: width,
            text,
        });
    }
    (drawings, courtyard)
}

/// `(model "path" (offset (xyz X Y Z)) (scale (xyz X Y Z)) (rotate (xyz X Y Z)))`.
fn map_models(form: &SNode) -> Vec<Model3D> {
    let mut out = Vec::new();
    for m in find_children(form, "model") {
        let body: Vec<&SNode> = body_children(m).collect();
        let path = body
            .first()
            .and_then(|n| atom_str(n))
            .unwrap_or("")
            .to_string();
        let offset = find_child(m, "offset")
            .and_then(|o| find_child(o, "xyz"))
            .map_or((0.0, 0.0, 0.0), read_xyz);
        let scale = find_child(m, "scale")
            .and_then(|s| find_child(s, "xyz"))
            .map_or((1.0, 1.0, 1.0), read_xyz);
        let rotate = find_child(m, "rotate")
            .and_then(|r| find_child(r, "xyz"))
            .map_or((0.0, 0.0, 0.0), read_xyz);
        out.push(Model3D {
            path,
            offset_mm: offset,
            scale,
            rotate_deg: rotate,
        });
    }
    out
}

fn read_xyz(form: &SNode) -> (f64, f64, f64) {
    let body: Vec<&SNode> = body_children(form).collect();
    let x = body.first().and_then(|n| atom_f64(n)).unwrap_or(0.0);
    let y = body.get(1).and_then(|n| atom_f64(n)).unwrap_or(0.0);
    let z = body.get(2).and_then(|n| atom_f64(n)).unwrap_or(0.0);
    (x, y, z)
}

/// `(segment (start x y) (end x y) (width w) (layer "F.Cu") (net N) [(locked)] (uuid …))`
fn map_tracks(root: &SNode, pcb: &mut Pcb, net_names: &HashMap<i32, String>) {
    for form in find_children(root, "segment") {
        let start = find_child(form, "start").map_or((0.0, 0.0), read_point);
        let end = find_child(form, "end").map_or((0.0, 0.0), read_point);
        let width = find_child(form, "width")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let layer = find_child(form, "layer")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("F.Cu")
            .to_string();
        let net = read_net_ref(form, net_names);
        let locked = find_child(form, "locked").is_some();
        let uuid = find_child(form, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        pcb.tracks.push(Track {
            uuid,
            layer: LayerRef(layer),
            net,
            points_mm: vec![start, end],
            width_mm: width,
            locked,
        });
    }
}

/// `(via [blind|buried] (at x y) (size d) (drill d) (layers "F.Cu" "B.Cu") (net N) [(locked)] (uuid …))`
fn map_vias(root: &SNode, pcb: &mut Pcb, net_names: &HashMap<i32, String>) {
    for form in find_children(root, "via") {
        // Kind is a bareword right after `(via`. Inspect the children
        // directly; `body_children` skips the head, so the first body
        // is either `blind`/`buried` or the first sub-form.
        let body: Vec<&SNode> = body_children(form).collect();
        let kind = body
            .first()
            .and_then(|n| atom_str(n))
            .filter(|s| matches!(*s, "blind" | "buried"))
            .map(String::from)
            .unwrap_or_default();
        let (x, y, _) = read_at(form);
        let drill = find_child(form, "drill")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let diameter = find_child(form, "size")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let layers: Vec<String> = find_child(form, "layers")
            .map(|n| {
                body_children(n)
                    .filter_map(|c| atom_str(c).map(String::from))
                    .collect()
            })
            .unwrap_or_default();
        let from_layer = layers
            .first()
            .cloned()
            .unwrap_or_else(|| "F.Cu".to_string());
        let to_layer = layers.get(1).cloned().unwrap_or_else(|| "B.Cu".to_string());
        let net = read_net_ref(form, net_names);
        let locked = find_child(form, "locked").is_some();
        let uuid = find_child(form, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        pcb.vias.push(Via {
            uuid,
            net,
            position_mm: (x, y),
            from_layer: LayerRef(from_layer),
            to_layer: LayerRef(to_layer),
            drill_mm: drill,
            diameter_mm: diameter,
            kind,
            locked,
        });
    }
}

/// `(zone (net N) (net_name …) (layer …) (uuid …) [(hatched)]
///   (connect_pads [MODE] (clearance X)) (min_thickness X)
///   [(thermal_gap X)] [(thermal_bridge_width X)]
///   (polygon (pts …)) …  (filled_polygon …) …)`
fn map_zones(root: &SNode, pcb: &mut Pcb, net_names: &HashMap<i32, String>) {
    for form in find_children(root, "zone") {
        let net = read_net_ref(form, net_names);
        let layer = find_child(form, "layer")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("F.Cu")
            .to_string();
        let uuid = find_child(form, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();

        let hatched = find_child(form, "hatched").is_some();

        // `(connect_pads …)` — the first body atom (if any) is the
        // mode; otherwise the form is just `(connect_pads (clearance X))`.
        let mut connect_pads = "yes".to_string();
        let mut thermal_relief = false;
        let mut clearance_mm = 0.0;
        if let Some(cp) = find_child(form, "connect_pads") {
            let cp_body: Vec<&SNode> = body_children(cp).collect();
            if let Some(first) = cp_body.first() {
                if let Some(s) = atom_str(first) {
                    if matches!(s, "yes" | "no" | "thru_hole_only" | "thermal_reliefs") {
                        connect_pads = s.to_string();
                        if s == "thermal_reliefs" {
                            thermal_relief = true;
                        }
                    }
                }
            }
            if let Some(c) = find_child(cp, "clearance") {
                clearance_mm = body_children(c).next().and_then(atom_f64).unwrap_or(0.0);
            }
        } else {
            // Older fixtures may omit the form entirely — KiCad's
            // default is `yes` with thermal reliefs disabled.
        }

        let min_thickness = find_child(form, "min_thickness")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);

        let thermal_gap = find_child(form, "thermal_gap")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);
        let thermal_bridge_width = find_child(form, "thermal_bridge_width")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .unwrap_or(0.0);

        // KiCad allows multiple polygon forms per zone (cutouts). We
        // take the first as the outer outline and treat the rest as
        // cutouts.
        let mut polygons: Vec<Vec<(f64, f64)>> = Vec::new();
        for poly in find_children(form, "polygon") {
            if let Some(pts) = find_child(poly, "pts") {
                polygons.push(collect_xy_points(pts));
            }
        }
        let outline_mm = polygons.first().cloned().unwrap_or_default();
        let cutouts_mm: Vec<Vec<(f64, f64)>> = polygons.into_iter().skip(1).collect();

        let filled_polygons: Vec<Vec<(f64, f64)>> = find_children(form, "filled_polygon")
            .iter()
            .filter_map(|p| find_child(p, "pts").map(collect_xy_points))
            .collect();

        pcb.zones.push(Zone {
            uuid,
            layer: LayerRef(layer),
            net,
            outline_mm,
            cutouts_mm,
            hatched,
            clearance_mm,
            thermal_relief,
            thermal_gap_mm: thermal_gap,
            thermal_bridge_width_mm: thermal_bridge_width,
            min_thickness_mm: min_thickness,
            connect_pads,
            filled_polygons,
        });
    }
}

/// Board outline = the union of `(gr_line …)` segments on `Edge.Cuts`.
/// For M0 we collect the per-segment endpoints in declaration order; a
/// real polygon-stitching pass lands with `crates/cad` polygon support
/// (M0-R-07).
fn map_outline(root: &SNode, pcb: &mut Pcb) {
    let mut points: Vec<(f64, f64)> = Vec::new();
    for line in find_children(root, "gr_line") {
        let on_edge = find_child(line, "layer")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            == Some("Edge.Cuts");
        if !on_edge {
            continue;
        }
        if let Some(start) = find_child(line, "start") {
            points.push(read_point(start));
        }
        if let Some(end) = find_child(line, "end") {
            points.push(read_point(end));
        }
    }
    pcb.outline = Outline {
        points_mm: points,
        cutouts: Vec::new(),
    };
}

/// All `(gr_text …)`, `(gr_line …)` (non-Edge.Cuts), `(gr_arc …)`,
/// `(gr_rect …)` lift into [`Drawing`] in source declaration order so
/// the canonical emitter can preserve sibling ordering on round-trip.
/// Edge.Cuts lines are claimed by [`map_outline`] and skipped here.
fn map_drawings(root: &SNode, pcb: &mut Pcb) {
    let drawing_heads = ["gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_text"];
    let SNode::List { children, .. } = root else {
        return;
    };
    for form in children {
        let head = match form.head_symbol() {
            Some(h) if drawing_heads.contains(&h) => h,
            _ => continue,
        };
        let layer = find_child(form, "layer")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        if head == "gr_line" && layer == "Edge.Cuts" {
            continue;
        }
        let width = find_child(form, "width")
            .and_then(|n| body_children(n).next())
            .and_then(atom_f64)
            .or_else(|| {
                find_child(form, "stroke")
                    .and_then(|s| find_child(s, "width"))
                    .and_then(|n| body_children(n).next())
                    .and_then(atom_f64)
            })
            .unwrap_or(0.0);
        let uuid = find_child(form, "uuid")
            .and_then(|n| body_children(n).next())
            .and_then(atom_str)
            .unwrap_or("")
            .to_string();
        let text = if head == "gr_text" {
            body_children(form)
                .find_map(atom_str)
                .unwrap_or("")
                .to_string()
        } else {
            String::new()
        };
        let points = read_drawing_points(form, head);
        pcb.drawings.push(Drawing {
            uuid,
            layer: LayerRef(layer),
            kind: head.to_string(),
            points_mm: points,
            width_mm: width,
            text,
        });
    }
}

/// Read geometry points from a drawing form in the order the canonical
/// emitter writes them. Order is per-kind: `gr_line`/`fp_line` use
/// `(start)` then `(end)`; `gr_circle`/`fp_circle` use `(center)` then
/// `(end)`; `gr_arc`/`fp_arc` use `(start)`, `(mid)`, `(end)`;
/// `gr_text`/`fp_text` use just `(at)`. Unrecognized kinds fall back to
/// a fixed scan order to preserve any geometry present.
fn read_drawing_points(form: &SNode, kind: &str) -> Vec<(f64, f64)> {
    let order: &[&str] = match kind {
        "gr_line" | "gr_rect" | "fp_line" | "fp_rect" => &["start", "end"],
        "gr_circle" | "fp_circle" => &["center", "end"],
        "gr_arc" | "fp_arc" => &["start", "mid", "end"],
        "gr_text" | "fp_text" => &["at"],
        _ => &["start", "end", "center", "mid", "at"],
    };
    let mut points = Vec::new();
    for tag in order {
        if let Some(p) = find_child(form, tag) {
            points.push(read_point(p));
        }
    }
    if let Some(pts) = find_child(form, "pts") {
        points.extend(collect_xy_points(pts));
    }
    points
}

/// `(at x y [rot])` — return (x, y, rot). All omitted fields default to
/// 0.0. The caller decides whether rotation is meaningful.
fn read_at(parent: &SNode) -> (f64, f64, f64) {
    find_child(parent, "at").map_or((0.0, 0.0, 0.0), |n| {
        let body: Vec<&SNode> = body_children(n).collect();
        let x = body.first().and_then(|n| atom_f64(n)).unwrap_or(0.0);
        let y = body.get(1).and_then(|n| atom_f64(n)).unwrap_or(0.0);
        let rot = body.get(2).and_then(|n| atom_f64(n)).unwrap_or(0.0);
        (x, y, rot)
    })
}

/// `(start x y)` / `(end x y)` / etc.
fn read_point(form: &SNode) -> (f64, f64) {
    let body: Vec<&SNode> = body_children(form).collect();
    let x = body.first().and_then(|n| atom_f64(n)).unwrap_or(0.0);
    let y = body.get(1).and_then(|n| atom_f64(n)).unwrap_or(0.0);
    (x, y)
}

/// `(net N)` inside a track/via/zone → look up the named net via the
/// net id table. Returns the empty string for unknown / unbound nets.
fn read_net_ref(form: &SNode, net_names: &HashMap<i32, String>) -> String {
    let Some(net_form) = find_child(form, "net") else {
        return String::new();
    };
    let body: Vec<&SNode> = body_children(net_form).collect();
    match body.first() {
        Some(first) => {
            if let Some(id) = atom_i32(first) {
                net_names.get(&id).cloned().unwrap_or_default()
            } else {
                // `(net "name")` form — used inside zones in older KiCad.
                atom_str(first).unwrap_or("").to_string()
            }
        }
        None => String::new(),
    }
}

// ---------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------

#[cfg(test)]
mod tests {
    // Float comparisons in these tests check that values parsed from
    // string literals survive the s-expression → KCIR mapping exactly.
    // The needless-raw-string-hashes lint fires inconsistently across
    // fixtures (some have embedded `"`, some don't); using `r#"..."#`
    // uniformly is more readable than splitting on quote presence.
    #![allow(clippy::float_cmp, clippy::needless_raw_string_hashes)]
    use super::*;
    use crate::sexpr::parse_str;

    fn parse_pcb_text(text: &str) -> Pcb {
        let nodes = parse_str(text).expect("parses");
        let root = nodes.first().expect("at least one form");
        map_pcb(root).expect("map_pcb")
    }

    #[test]
    fn parses_pad_with_drill_layers_and_net() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108) (generator kiclaude)
                  (net 0 "")
                  (net 1 "+3V3")
                  (footprint "Resistor_SMD:R_0603"
                    (layer "F.Cu") (uuid "u-1") (at 50 50 0)
                    (pad "1" smd roundrect
                      (at -0.8 0)
                      (size 0.95 0.95)
                      (layers "F.Cu" "F.Mask")
                      (roundrect_rratio 0.25)
                      (net 1 "+3V3")
                      (uuid "p-1")
                    )
                  )
                )"#,
        );
        assert_eq!(pcb.footprints.len(), 1);
        let fp = &pcb.footprints[0];
        assert_eq!(fp.pads.len(), 1);
        let pad = &fp.pads[0];
        assert_eq!(pad.number, "1");
        assert_eq!(pad.pad_type, "smd");
        assert_eq!(pad.shape, "roundrect");
        assert_eq!(pad.position_mm, (-0.8, 0.0));
        assert_eq!(pad.size_mm, (0.95, 0.95));
        assert_eq!(pad.layers.len(), 2);
        assert_eq!(pad.roundrect_rratio, Some(0.25));
        assert_eq!(pad.net, "+3V3");
        assert_eq!(pad.uuid, "p-1");
    }

    #[test]
    fn parses_thru_hole_pad_with_drill() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108) (generator kiclaude)
                  (net 0 "")
                  (footprint "Connector:PinHeader_1x04"
                    (layer "F.Cu") (uuid "u-1") (at 0 0 0)
                    (pad "1" thru_hole circle
                      (at 0 0)
                      (size 1.7 1.7)
                      (drill 1.0)
                      (layers "*.Cu" "*.Mask")
                      (uuid "p-1")
                    )
                  )
                )"#,
        );
        let pad = &pcb.footprints[0].pads[0];
        assert_eq!(pad.pad_type, "thru_hole");
        assert_eq!(pad.drill_mm, Some((1.0, 1.0)));
    }

    #[test]
    fn parses_oval_drill_pad() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108) (generator kiclaude)
                  (footprint "Connector:Slotted"
                    (layer "F.Cu") (uuid "u-1") (at 0 0 0)
                    (pad "1" thru_hole oval
                      (at 0 0)
                      (size 2.0 1.5)
                      (drill oval 1.2 0.8)
                      (layers "*.Cu")
                      (uuid "p-1")
                    )
                  )
                )"#,
        );
        let pad = &pcb.footprints[0].pads[0];
        assert_eq!(pad.drill_mm, Some((1.2, 0.8)));
    }

    #[test]
    fn parses_courtyard_from_fp_poly() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (footprint "Resistor_SMD:R_0603"
                    (layer "F.Cu") (uuid "u-1") (at 0 0 0)
                    (fp_poly
                      (pts (xy -1.7 -0.9) (xy 1.7 -0.9) (xy 1.7 0.9) (xy -1.7 0.9))
                      (stroke (width 0.05) (type default))
                      (fill none)
                      (layer "F.CrtYd")
                      (uuid "")
                    )
                  )
                )"#,
        );
        let fp = &pcb.footprints[0];
        let crt = fp.courtyard.as_ref().expect("courtyard");
        assert_eq!(crt.layer.0, "F.CrtYd");
        assert_eq!(crt.points_mm.len(), 4);
        assert_eq!(crt.width_mm, 0.05);
    }

    #[test]
    fn parses_model_3d() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (footprint "Resistor_SMD:R_0603"
                    (layer "F.Cu") (uuid "u-1") (at 0 0 0)
                    (model "${KICAD9_3DMODEL_DIR}/R_0603.step"
                      (offset (xyz 0 0 0))
                      (scale (xyz 1 1 1))
                      (rotate (xyz 0 0 0))
                    )
                  )
                )"#,
        );
        let fp = &pcb.footprints[0];
        assert_eq!(fp.models_3d.len(), 1);
        assert_eq!(fp.models_3d[0].path, "${KICAD9_3DMODEL_DIR}/R_0603.step");
        assert_eq!(fp.models_3d[0].scale, (1.0, 1.0, 1.0));
    }

    #[test]
    fn parses_attributes_and_locked() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (footprint "Resistor_SMD:R_0603"
                    (layer "F.Cu") (uuid "u-1") (at 0 0 0)
                    (attr smd locked)
                  )
                )"#,
        );
        let fp = &pcb.footprints[0];
        assert_eq!(fp.attributes, vec!["smd".to_string(), "locked".to_string()]);
        assert!(fp.locked, "locked flag derived from attr");
    }

    #[test]
    fn parses_top_level_net_class() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (net_class "Default" "Default class"
                    (clearance 0.2)
                    (trace_width 0.25)
                    (via_dia 0.6)
                    (via_drill 0.3)
                  )
                  (net_class "DiffPair" "USB / LVDS"
                    (clearance 0.15)
                    (trace_width 0.2)
                    (via_dia 0.5)
                    (via_drill 0.25)
                    (diff_pair_width 0.18)
                    (diff_pair_gap 0.12)
                  )
                )"#,
        );
        assert_eq!(pcb.net_classes.len(), 2);
        assert_eq!(pcb.net_classes[0].name, "Default");
        assert_eq!(pcb.net_classes[0].trace_width_mm, 0.25);
        assert_eq!(pcb.net_classes[1].diff_pair_width_mm, Some(0.18));
        assert_eq!(pcb.net_classes[1].diff_pair_gap_mm, Some(0.12));
    }

    #[test]
    fn parses_solder_mask_min_width() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (setup
                    (pad_to_mask_clearance 0.05)
                    (solder_mask_min_width 0.1)
                  )
                )"#,
        );
        assert_eq!(pcb.pad_to_mask_clearance_mm, 0.05);
        assert_eq!(pcb.solder_mask_min_width_mm, 0.1);
    }

    #[test]
    fn parses_locked_track() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (net 0 "")
                  (net 1 "VCC")
                  (segment
                    (start 0 0) (end 1 0) (width 0.25) (layer "F.Cu") (net 1) (locked)
                    (uuid "t1")
                  )
                )"#,
        );
        assert_eq!(pcb.tracks.len(), 1);
        assert!(pcb.tracks[0].locked);
    }

    #[test]
    fn parses_blind_via() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (net 0 "")
                  (via blind
                    (at 5 5) (size 0.6) (drill 0.3) (layers "F.Cu" "In1.Cu") (net 0)
                    (uuid "v1")
                  )
                )"#,
        );
        assert_eq!(pcb.vias.len(), 1);
        assert_eq!(pcb.vias[0].kind, "blind");
        assert_eq!(pcb.vias[0].to_layer.0, "In1.Cu");
    }

    #[test]
    fn parses_zone_with_cutouts_and_thermal_settings() {
        let pcb = parse_pcb_text(
            r#"(kicad_pcb (version 20240108)
                  (net 0 "")
                  (net 1 "GND")
                  (zone (net 1) (net_name "GND") (layer "F.Cu") (uuid "z1")
                    (hatched)
                    (connect_pads thermal_reliefs (clearance 0.5))
                    (min_thickness 0.25)
                    (thermal_gap 0.4)
                    (thermal_bridge_width 0.3)
                    (polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
                    (polygon (pts (xy 2 2) (xy 4 2) (xy 4 4) (xy 2 4)))
                    (filled_polygon (layer "F.Cu") (pts (xy 0 0) (xy 10 0) (xy 5 10)))
                  )
                )"#,
        );
        let z = &pcb.zones[0];
        assert!(z.hatched);
        assert!(z.thermal_relief);
        assert_eq!(z.connect_pads, "thermal_reliefs");
        assert_eq!(z.clearance_mm, 0.5);
        assert_eq!(z.min_thickness_mm, 0.25);
        assert_eq!(z.thermal_gap_mm, 0.4);
        assert_eq!(z.thermal_bridge_width_mm, 0.3);
        assert_eq!(z.outline_mm.len(), 4);
        assert_eq!(z.cutouts_mm.len(), 1);
        assert_eq!(z.cutouts_mm[0].len(), 4);
        assert_eq!(z.filled_polygons.len(), 1);
    }

    #[test]
    fn rejects_non_kicad_pcb_root() {
        let nodes = parse_str("(kicad_sch (version 1))").expect("parses");
        let err = map_pcb(&nodes[0]).expect_err("must fail");
        assert!(err.contains("expected (kicad_pcb"));
    }
}
