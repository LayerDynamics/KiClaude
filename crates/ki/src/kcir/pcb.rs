//! PCB view: footprints (with pads, courtyards, 3D models), tracks, vias,
//! zones, layers, drawings, and the per-PCB net-class table.
//!
//! M2-R-03 — full editing surface. Every type carries `serde` + `ts-rs`
//! derives so the React frontend imports the same shape the Rust core
//! exposes.

use serde::{Deserialize, Serialize};

use super::nets::{LayerRef, Net, NetClass, NetClassRef};

/// The PCB view of a [`Project`](super::Project).
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Pcb {
    /// `(version YYYYMMDD)` — the `KiCad` `.kicad_pcb` schema stamp.
    #[serde(default)]
    pub version: u32,
    /// `(generator <name>)` — name of the tool that last wrote this file.
    #[serde(default)]
    pub generator: String,
    /// `(general (thickness X))` — total board thickness in mm.
    #[serde(default)]
    pub thickness_mm: f64,
    /// `(paper "<format>")` — page size for plotted output.
    #[serde(default)]
    pub paper: String,
    /// `(setup (pad_to_mask_clearance X))` — pad-to-mask clearance in mm.
    #[serde(default)]
    pub pad_to_mask_clearance_mm: f64,
    /// `(setup (solder_mask_min_width X))` — minimum sliver between mask
    /// openings before `KiCad` merges them.
    #[serde(default)]
    pub solder_mask_min_width_mm: f64,
    /// Per-PCB `(net_class …)` declarations.
    #[serde(default)]
    pub net_classes: Vec<NetClass>,
    pub layers: Vec<Layer>,
    pub footprints: Vec<FootprintInstance>,
    pub tracks: Vec<Track>,
    pub vias: Vec<Via>,
    pub zones: Vec<Zone>,
    pub outline: Outline,
    pub drawings: Vec<Drawing>,
    pub nets: Vec<Net>,
}

/// A board layer — copper, dielectric, soldermask, silkscreen, etc.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Layer {
    /// `KiCad` layer id — 0 = F.Cu, 31 = B.Cu, 32+ = user / mask / paste.
    pub id: i32,
    pub name: String,
    /// `signal`, `power`, `mixed`, `jumper`, `user`.
    pub kind: String,
    /// Optional display name (e.g. `"B.Adhesive"`).
    pub purpose: String,
}

/// An instance of a library footprint placed on the board.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct FootprintInstance {
    pub uuid: String,
    pub refdes: String,
    pub lib_id: String,
    pub value: String,
    pub mpn: String,
    pub layer: LayerRef,
    pub position_mm: (f64, f64),
    pub rotation_deg: f64,
    pub locked: bool,
    /// `(attr …)` flags — `smd`, `through_hole`, `exclude_from_pos_files`,
    /// `exclude_from_bom`. Empty if the source omitted the form.
    #[serde(default)]
    pub attributes: Vec<String>,
    /// All `(pad …)` entries belonging to this footprint, in declaration
    /// order. M2-R-01 populates these from the source S-expression.
    #[serde(default)]
    pub pads: Vec<Pad>,
    /// Courtyard polygon on `F.CrtYd` / `B.CrtYd`. Used by the M2-R-06
    /// DRC kernel's courtyard-collision check.
    #[serde(default)]
    pub courtyard: Option<FootprintCourtyard>,
    /// `(model "<path>" (offset …) (scale …) (rotate …))` blocks. We
    /// preserve them for round-trip but the schematic/PCB editors don't
    /// touch them.
    #[serde(default)]
    pub models_3d: Vec<Model3D>,
    /// Free-form silkscreen / fab annotations inside the footprint, kept
    /// for round-trip fidelity.
    #[serde(default)]
    pub drawings: Vec<Drawing>,
}

/// One pad on a [`FootprintInstance`].
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Pad {
    /// `KiCad`'s pad number — usually `"1"`, `"GND"`, etc. Not unique
    /// across the board; combine with the parent's `refdes` for a
    /// project-wide id.
    pub number: String,
    /// `smd`, `thru_hole`, `connect`, `np_thru_hole`.
    pub pad_type: String,
    /// `rect`, `circle`, `oval`, `roundrect`, `trapezoid`, `custom`.
    pub shape: String,
    /// Pad center in the parent footprint's local frame, mm.
    pub position_mm: (f64, f64),
    /// Rotation in degrees relative to the parent footprint.
    pub rotation_deg: f64,
    /// `(size W H)` — copper extent in mm.
    pub size_mm: (f64, f64),
    /// `(drill D)` for through-hole pads, or `(drill oval W H)`.
    pub drill_mm: Option<(f64, f64)>,
    /// Layers the pad lives on (`F.Cu`, `*.Cu`, `F.Mask`, …).
    pub layers: Vec<LayerRef>,
    /// `(net N "<name>")` — the resolved net name, or empty for NC.
    pub net: String,
    /// `(roundrect_rratio R)` — corner radius ratio for rounded
    /// rectangle pads.
    #[serde(default)]
    pub roundrect_rratio: Option<f64>,
    /// `(uuid …)` — opaque per-instance identifier.
    pub uuid: String,
}

/// Footprint courtyard polygon (F.CrtYd or B.CrtYd).
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct FootprintCourtyard {
    pub layer: LayerRef,
    pub points_mm: Vec<(f64, f64)>,
    /// Optional line width in mm.
    pub width_mm: f64,
}

/// `(model "<path>" …)` — a 3D model the renderer can mount on the pad.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Model3D {
    pub path: String,
    pub offset_mm: (f64, f64, f64),
    pub scale: (f64, f64, f64),
    pub rotate_deg: (f64, f64, f64),
}

impl Model3D {
    /// `KiCad`'s default identity transform.
    #[must_use]
    pub fn identity(path: impl Into<String>) -> Self {
        Self {
            path: path.into(),
            offset_mm: (0.0, 0.0, 0.0),
            scale: (1.0, 1.0, 1.0),
            rotate_deg: (0.0, 0.0, 0.0),
        }
    }
}

/// A copper track segment.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Track {
    pub uuid: String,
    pub layer: LayerRef,
    pub net: String,
    pub points_mm: Vec<(f64, f64)>,
    pub width_mm: f64,
    /// `true` when the track is marked `(locked)` in the source.
    #[serde(default)]
    pub locked: bool,
}

/// A via connecting two or more copper layers.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Via {
    pub uuid: String,
    pub net: String,
    pub position_mm: (f64, f64),
    pub from_layer: LayerRef,
    pub to_layer: LayerRef,
    pub drill_mm: f64,
    pub diameter_mm: f64,
    /// `blind`, `buried`, or empty for through-hole.
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub locked: bool,
}

/// A copper zone (polygon pour) bound to a net.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Zone {
    pub uuid: String,
    pub layer: LayerRef,
    pub net: String,
    /// Outer outline points; cutouts hang off `cutouts_mm`.
    pub outline_mm: Vec<(f64, f64)>,
    /// Holes inside the zone outline (`KiCad` zones can have multiple).
    #[serde(default)]
    pub cutouts_mm: Vec<Vec<(f64, f64)>>,
    /// True when the zone is hatched (Hatched / `Hatch_None`) — `KiCad`'s
    /// default is filled solid.
    #[serde(default)]
    pub hatched: bool,
    /// Net clearance override in mm (0 = inherit from net class).
    #[serde(default)]
    pub clearance_mm: f64,
    /// Minimum spoke width for thermal reliefs in mm.
    #[serde(default)]
    pub thermal_relief: bool,
    /// `(thermal_gap X) (thermal_bridge_width Y)` — only meaningful
    /// when `thermal_relief` is true.
    #[serde(default)]
    pub thermal_gap_mm: f64,
    #[serde(default)]
    pub thermal_bridge_width_mm: f64,
    /// `(min_thickness X)` — minimum slot width inside the fill.
    #[serde(default)]
    pub min_thickness_mm: f64,
    /// `(connect_pads …)` mode — `yes`, `no`, `thru_hole_only`.
    /// Default `yes`.
    #[serde(default = "default_connect_pads")]
    pub connect_pads: String,
    /// Computed fill polygons (one or more — `KiCad` emits a separate
    /// `(filled_polygon …)` per disconnected region). M2-R-05 zone-fill
    /// populates these from the outline + obstacles.
    #[serde(default)]
    pub filled_polygons: Vec<Vec<(f64, f64)>>,
}

fn default_connect_pads() -> String {
    "yes".to_string()
}

impl Default for Zone {
    fn default() -> Self {
        Self {
            uuid: String::new(),
            layer: LayerRef::default(),
            net: String::new(),
            outline_mm: Vec::new(),
            cutouts_mm: Vec::new(),
            hatched: false,
            clearance_mm: 0.0,
            thermal_relief: false,
            thermal_gap_mm: 0.0,
            thermal_bridge_width_mm: 0.0,
            min_thickness_mm: 0.0,
            connect_pads: default_connect_pads(),
            filled_polygons: Vec::new(),
        }
    }
}

/// The board outline (Edge.Cuts polygon).
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Outline {
    pub points_mm: Vec<(f64, f64)>,
    pub cutouts: Vec<Vec<(f64, f64)>>,
}

/// Non-conductive graphics: silkscreen, fab, courtyard.
#[cfg_attr(feature = "ts-export", derive(ts_rs::TS))]
#[cfg_attr(feature = "ts-export", ts(export))]
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Drawing {
    pub uuid: String,
    pub layer: LayerRef,
    /// `gr_line`, `gr_arc`, `gr_rect`, `gr_circle`, `gr_text`,
    /// `fp_line`, `fp_text` (when inside a footprint).
    pub kind: String,
    pub points_mm: Vec<(f64, f64)>,
    pub width_mm: f64,
    pub text: String,
}

// ---------------------------------------------------------------------
// Invariants. Pure functions over a `Pcb` snapshot. Used by tests and
// by the M2-R-06 DRC kernel as a structural pre-check.
// ---------------------------------------------------------------------

/// Errors produced by [`check_invariants`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PcbInvariantError {
    /// A track / via / zone names a net not declared in `Pcb::nets`.
    UnknownNet {
        kind: &'static str,
        uuid: String,
        net: String,
    },
    /// A track / via / zone names a layer not declared in `Pcb::layers`.
    UnknownLayer {
        kind: &'static str,
        uuid: String,
        layer: String,
    },
    /// The copper layer stack is out of order — `KiCad` assumes ids
    /// strictly increase from F.Cu (0) to B.Cu (31).
    LayersOutOfOrder {
        previous_id: i32,
        next_id: i32,
        previous_name: String,
        next_name: String,
    },
    /// A net references a net class that isn't declared in `net_classes`.
    UnknownNetClass {
        net: String,
        class: String,
    },
    /// Two footprints share a refdes. KCIR allows it (KC005 covers it
    /// at schematic level) but the M2 DRC kernel needs unique refdes
    /// for pad addressing.
    DuplicateRefdes(String),
    /// A pad's net is non-empty but the parent footprint has no refdes,
    /// so the pad cannot be addressed.
    PadWithoutParentRefdes {
        footprint_uuid: String,
        pad_number: String,
    },
}

impl core::fmt::Display for PcbInvariantError {
    fn fmt(&self, f: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        match self {
            PcbInvariantError::UnknownNet { kind, uuid, net } => {
                write!(f, "{kind} {uuid}: unknown net {net:?}")
            }
            PcbInvariantError::UnknownLayer { kind, uuid, layer } => {
                write!(f, "{kind} {uuid}: unknown layer {layer:?}")
            }
            PcbInvariantError::LayersOutOfOrder {
                previous_id,
                next_id,
                previous_name,
                next_name,
            } => write!(
                f,
                "layer stack out of order: {previous_name} (id {previous_id}) appears before {next_name} (id {next_id})",
            ),
            PcbInvariantError::UnknownNetClass { net, class } => {
                write!(f, "net {net:?} references undeclared class {class:?}")
            }
            PcbInvariantError::DuplicateRefdes(refdes) => {
                write!(f, "duplicate footprint refdes {refdes:?}")
            }
            PcbInvariantError::PadWithoutParentRefdes {
                footprint_uuid,
                pad_number,
            } => write!(
                f,
                "footprint {footprint_uuid}: pad {pad_number:?} has a net but parent has no refdes",
            ),
        }
    }
}

impl std::error::Error for PcbInvariantError {}

/// Walk the [`Pcb`] and report every structural violation. Empty `Vec`
/// = invariants hold.
#[must_use]
pub fn check_invariants(pcb: &Pcb) -> Vec<PcbInvariantError> {
    let mut errors = Vec::new();
    let net_names: std::collections::HashSet<&str> =
        pcb.nets.iter().map(|n| n.name.as_str()).collect();
    let layer_names: std::collections::HashSet<&str> =
        pcb.layers.iter().map(|l| l.name.as_str()).collect();
    let class_names: std::collections::HashSet<&str> =
        pcb.net_classes.iter().map(|c| c.name.as_str()).collect();

    check_layer_order(pcb, &mut errors);
    check_tracks(pcb, &net_names, &layer_names, &mut errors);
    check_vias(pcb, &net_names, &layer_names, &mut errors);
    check_zones(pcb, &net_names, &layer_names, &mut errors);
    check_net_classes(pcb, &class_names, &mut errors);
    check_footprints(pcb, &mut errors);
    errors
}

fn check_layer_order(pcb: &Pcb, errors: &mut Vec<PcbInvariantError>) {
    // Copper layer stack ordering: among layers whose `kind` is `signal`
    // / `mixed` / `power` / `jumper`, ids must monotonically increase
    // in declaration order.
    let mut copper_seen: Option<(i32, &str)> = None;
    for layer in &pcb.layers {
        if matches!(layer.kind.as_str(), "signal" | "mixed" | "power" | "jumper") {
            if let Some((prev_id, prev_name)) = copper_seen {
                if layer.id <= prev_id {
                    errors.push(PcbInvariantError::LayersOutOfOrder {
                        previous_id: prev_id,
                        next_id: layer.id,
                        previous_name: prev_name.to_string(),
                        next_name: layer.name.clone(),
                    });
                }
            }
            copper_seen = Some((layer.id, layer.name.as_str()));
        }
    }
}

fn check_tracks(
    pcb: &Pcb,
    net_names: &std::collections::HashSet<&str>,
    layer_names: &std::collections::HashSet<&str>,
    errors: &mut Vec<PcbInvariantError>,
) {
    for t in &pcb.tracks {
        if !t.net.is_empty() && !net_names.contains(t.net.as_str()) {
            errors.push(PcbInvariantError::UnknownNet {
                kind: "track",
                uuid: t.uuid.clone(),
                net: t.net.clone(),
            });
        }
        let l = &t.layer.0;
        if !l.is_empty() && !layer_names.contains(l.as_str()) {
            errors.push(PcbInvariantError::UnknownLayer {
                kind: "track",
                uuid: t.uuid.clone(),
                layer: l.clone(),
            });
        }
    }
}

fn check_vias(
    pcb: &Pcb,
    net_names: &std::collections::HashSet<&str>,
    layer_names: &std::collections::HashSet<&str>,
    errors: &mut Vec<PcbInvariantError>,
) {
    for v in &pcb.vias {
        if !v.net.is_empty() && !net_names.contains(v.net.as_str()) {
            errors.push(PcbInvariantError::UnknownNet {
                kind: "via",
                uuid: v.uuid.clone(),
                net: v.net.clone(),
            });
        }
        for layer in [&v.from_layer.0, &v.to_layer.0] {
            if !layer.is_empty() && !layer_names.contains(layer.as_str()) {
                errors.push(PcbInvariantError::UnknownLayer {
                    kind: "via",
                    uuid: v.uuid.clone(),
                    layer: layer.clone(),
                });
            }
        }
    }
}

fn check_zones(
    pcb: &Pcb,
    net_names: &std::collections::HashSet<&str>,
    layer_names: &std::collections::HashSet<&str>,
    errors: &mut Vec<PcbInvariantError>,
) {
    for z in &pcb.zones {
        if !z.net.is_empty() && !net_names.contains(z.net.as_str()) {
            errors.push(PcbInvariantError::UnknownNet {
                kind: "zone",
                uuid: z.uuid.clone(),
                net: z.net.clone(),
            });
        }
        let l = &z.layer.0;
        if !l.is_empty() && !layer_names.contains(l.as_str()) {
            errors.push(PcbInvariantError::UnknownLayer {
                kind: "zone",
                uuid: z.uuid.clone(),
                layer: l.clone(),
            });
        }
    }
}

fn check_net_classes(
    pcb: &Pcb,
    class_names: &std::collections::HashSet<&str>,
    errors: &mut Vec<PcbInvariantError>,
) {
    for net in &pcb.nets {
        let NetClassRef(class) = &net.class;
        if !class.is_empty() && !class_names.contains(class.as_str()) {
            errors.push(PcbInvariantError::UnknownNetClass {
                net: net.name.clone(),
                class: class.clone(),
            });
        }
    }
}

fn check_footprints(pcb: &Pcb, errors: &mut Vec<PcbInvariantError>) {
    let mut seen_refdes: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    for fp in &pcb.footprints {
        if !fp.refdes.is_empty() {
            let counter = seen_refdes.entry(fp.refdes.clone()).or_insert(0);
            *counter += 1;
        }
        for pad in &fp.pads {
            if !pad.net.is_empty() && fp.refdes.is_empty() {
                errors.push(PcbInvariantError::PadWithoutParentRefdes {
                    footprint_uuid: fp.uuid.clone(),
                    pad_number: pad.number.clone(),
                });
            }
        }
    }
    for (refdes, count) in seen_refdes {
        if count > 1 {
            errors.push(PcbInvariantError::DuplicateRefdes(refdes));
        }
    }
}

// ---------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kcir::nets::{LayerRef, NetClassRef};

    fn make_pcb() -> Pcb {
        Pcb {
            version: 20_240_108,
            generator: "kiclaude".to_string(),
            layers: vec![
                Layer {
                    id: 0,
                    name: "F.Cu".into(),
                    kind: "signal".into(),
                    purpose: String::new(),
                },
                Layer {
                    id: 31,
                    name: "B.Cu".into(),
                    kind: "signal".into(),
                    purpose: String::new(),
                },
            ],
            nets: vec![Net {
                name: "+3V3".into(),
                class: NetClassRef("Default".into()),
                ..Net::default()
            }],
            net_classes: vec![NetClass {
                name: "Default".into(),
                trace_width_mm: 0.25,
                clearance_mm: 0.2,
                ..NetClass::default()
            }],
            ..Pcb::default()
        }
    }

    #[test]
    fn check_invariants_passes_on_clean_pcb() {
        let pcb = make_pcb();
        let errors = check_invariants(&pcb);
        assert!(errors.is_empty(), "{errors:?}");
    }

    #[test]
    fn check_invariants_detects_unknown_net_on_track() {
        let mut pcb = make_pcb();
        pcb.tracks.push(Track {
            uuid: "tr-1".into(),
            layer: LayerRef("F.Cu".into()),
            net: "BOGUS".into(),
            points_mm: vec![(0.0, 0.0), (1.0, 0.0)],
            width_mm: 0.25,
            ..Track::default()
        });
        let errors = check_invariants(&pcb);
        assert_eq!(
            errors,
            vec![PcbInvariantError::UnknownNet {
                kind: "track",
                uuid: "tr-1".into(),
                net: "BOGUS".into(),
            }]
        );
    }

    #[test]
    fn check_invariants_detects_unknown_layer_on_via() {
        let mut pcb = make_pcb();
        pcb.vias.push(Via {
            uuid: "v-1".into(),
            net: "+3V3".into(),
            position_mm: (0.0, 0.0),
            from_layer: LayerRef("F.Cu".into()),
            to_layer: LayerRef("Internal.1".into()),
            drill_mm: 0.3,
            diameter_mm: 0.6,
            ..Via::default()
        });
        let errors = check_invariants(&pcb);
        assert!(errors
            .iter()
            .any(|e| matches!(e, PcbInvariantError::UnknownLayer { kind: "via", .. })));
    }

    #[test]
    fn check_invariants_detects_layer_stack_out_of_order() {
        let mut pcb = make_pcb();
        pcb.layers = vec![
            Layer {
                id: 31,
                name: "B.Cu".into(),
                kind: "signal".into(),
                purpose: String::new(),
            },
            Layer {
                id: 0,
                name: "F.Cu".into(),
                kind: "signal".into(),
                purpose: String::new(),
            },
        ];
        let errors = check_invariants(&pcb);
        assert!(matches!(
            errors.first(),
            Some(PcbInvariantError::LayersOutOfOrder { .. })
        ));
    }

    #[test]
    fn check_invariants_detects_unknown_net_class() {
        let mut pcb = make_pcb();
        pcb.nets[0].class = NetClassRef("DoesNotExist".into());
        let errors = check_invariants(&pcb);
        assert!(errors
            .iter()
            .any(|e| matches!(e, PcbInvariantError::UnknownNetClass { .. })));
    }

    #[test]
    fn check_invariants_detects_duplicate_refdes() {
        let mut pcb = make_pcb();
        pcb.footprints.push(FootprintInstance {
            uuid: "u-1".into(),
            refdes: "R1".into(),
            ..FootprintInstance::default()
        });
        pcb.footprints.push(FootprintInstance {
            uuid: "u-2".into(),
            refdes: "R1".into(),
            ..FootprintInstance::default()
        });
        let errors = check_invariants(&pcb);
        assert!(errors
            .iter()
            .any(|e| matches!(e, PcbInvariantError::DuplicateRefdes(r) if r == "R1")));
    }

    #[test]
    fn check_invariants_detects_pad_without_parent_refdes() {
        let mut pcb = make_pcb();
        pcb.footprints.push(FootprintInstance {
            uuid: "u-empty".into(),
            refdes: String::new(),
            pads: vec![Pad {
                number: "1".into(),
                net: "+3V3".into(),
                ..Pad::default()
            }],
            ..FootprintInstance::default()
        });
        let errors = check_invariants(&pcb);
        assert!(errors.iter().any(
            |e| matches!(e, PcbInvariantError::PadWithoutParentRefdes { pad_number, .. } if pad_number == "1")
        ));
    }

    #[test]
    fn default_zone_connect_pads_round_trips() {
        let z = Zone::default();
        let json = serde_json::to_string(&z).expect("ser");
        let back: Zone = serde_json::from_str(&json).expect("de");
        assert_eq!(z, back);
        // `default_connect_pads` must populate the field even on serde
        // default — so a round-trip through JSON never drops it to
        // empty.
        assert_eq!(back.connect_pads, "yes");
    }
}
