//! 3D STEP model placement → scene description — M3-R-06.
//!
//! Walks every footprint on a [`Pcb`](kiclaude_ki::kcir::Pcb), reads
//! its declared `Model3D` entries (path + offset + scale + rotate
//! triples), and produces a flat [`ThreeScene`] description ready
//! to consume from `packages/kithree` (the M3-T-06 three.js viewer).
//!
//! Each scene entry carries the model's `path` and an **absolute**
//! transform — the footprint's own position+rotation composed with
//! the model's per-footprint offset+rotate. That way the viewer
//! mounts each model with one matrix-multiply, no parent/child
//! chain to maintain.
//!
//! ## Coordinate convention
//!
//! `KiCad`'s PCB plane uses X-right / Y-down (screen coordinates).
//! `Model3D.offset_mm` is `(x, y, z)` in the footprint's local
//! frame; the on-board z is taken straight from the model offset
//! (typically positive = above the board top surface).
//!
//! ## Rotation composition
//!
//! `KiCad`'s `(rotate ...)` block carries Euler angles in degrees
//! `(rx, ry, rz)` — applied in **ZYX order** in pcbnew's renderer.
//! We pre-compose the footprint's flat `rotation_deg` (a Z-axis
//! rotation in the board plane) onto the model's local Z so the
//! scene entry's rotation is the final composed Euler triple. No
//! quaternion library needed — the math is closed-form because
//! the board's plane is fixed.

#![allow(clippy::cast_precision_loss)]

use serde::{Deserialize, Serialize};

use kiclaude_ki::kcir::{FootprintInstance, Pcb};

/// One model placement in the scene description.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScenePlacement {
    /// `path` from the source `Model3D` (e.g. `"${KICAD9_3DMODEL_DIR}/Resistor_SMD.3dshapes/R_0603.step"`).
    pub model_path: String,
    /// Refdes of the parent footprint — useful for the viewer's
    /// click-to-select handler.
    pub refdes: String,
    /// Absolute world-frame position in mm. `(x, y)` is the
    /// footprint's board position + the model's local offset;
    /// `z` is the model offset's z (or zero if unset).
    pub position_mm: (f64, f64, f64),
    /// Per-axis scale multiplier — passes the model's
    /// `(scale ...)` block through unchanged.
    pub scale: (f64, f64, f64),
    /// Composed Euler rotation `(rx_deg, ry_deg, rz_deg)`.
    /// `rz` includes the footprint's board-plane rotation; `rx`
    /// and `ry` come straight from the model.
    pub rotation_deg: (f64, f64, f64),
    /// Side hint — `"top"` if the footprint is on the top copper
    /// layer (F.Cu), `"bottom"` otherwise. Lets the viewer flip
    /// the model 180° about X for bottom-mounted parts.
    pub side: SceneSide,
}

/// Which side of the board the footprint sits on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SceneSide {
    Top,
    Bottom,
}

/// The full 3D scene for a board — board outline + footprint
/// placements. The viewer extrudes the outline into the board body
/// in its own renderer (we just hand it the 2D outline points).
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct ThreeScene {
    /// Total board thickness (mm). Used by the viewer to extrude
    /// the outline polygon into a board mesh.
    pub board_thickness_mm: f64,
    /// Outer board outline in board-frame mm, CCW. Empty if the
    /// project has no Edge.Cuts geometry.
    pub board_outline_mm: Vec<(f64, f64)>,
    /// Every footprint's resolved model placements. Footprints
    /// without any `Model3D` entry contribute nothing to this list.
    pub placements: Vec<ScenePlacement>,
}

/// Build a [`ThreeScene`] from a [`Pcb`]. Reads `Pcb.footprints`
/// for placements, `Pcb.outline.points_mm` for the board outline,
/// and `Pcb.thickness_mm` for the board body.
#[must_use]
pub fn scene_from_pcb(pcb: &Pcb) -> ThreeScene {
    let mut placements = Vec::new();
    for fp in &pcb.footprints {
        for model in &fp.models_3d {
            placements.push(placement_for_model(fp, model));
        }
    }
    ThreeScene {
        board_thickness_mm: pcb.thickness_mm,
        board_outline_mm: pcb.outline.points_mm.clone(),
        placements,
    }
}

fn placement_for_model(
    fp: &FootprintInstance,
    model: &kiclaude_ki::kcir::Model3D,
) -> ScenePlacement {
    // Footprint's board-plane rotation in radians.
    let fp_rot_rad = fp.rotation_deg.to_radians();
    let (sin_r, cos_r) = fp_rot_rad.sin_cos();
    // Rotate the model's local (x, y) offset into the board frame
    // before adding the footprint position.
    let (mx, my, mz) = model.offset_mm;
    let world_x = fp.position_mm.0 + mx * cos_r - my * sin_r;
    let world_y = fp.position_mm.1 + mx * sin_r + my * cos_r;
    let world_z = mz;

    // Compose the rotation: footprint's board rotation contributes
    // to rz (Z axis = board normal); rx/ry come straight from the
    // model and don't change.
    let (mrx, mry, mrz) = model.rotate_deg;
    let composed = (mrx, mry, mrz + fp.rotation_deg);

    let side = if fp.layer.0 == "B.Cu" {
        SceneSide::Bottom
    } else {
        SceneSide::Top
    };

    ScenePlacement {
        model_path: model.path.clone(),
        refdes: fp.refdes.clone(),
        position_mm: (world_x, world_y, world_z),
        scale: model.scale,
        rotation_deg: composed,
        side,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use kiclaude_ki::kcir::{FootprintInstance, Model3D, Pcb};

    fn fp_at(
        refdes: &str,
        position: (f64, f64),
        rotation_deg: f64,
        layer: &str,
        models: Vec<Model3D>,
    ) -> FootprintInstance {
        FootprintInstance {
            refdes: refdes.to_string(),
            position_mm: position,
            rotation_deg,
            layer: kiclaude_ki::kcir::LayerRef(layer.to_string()),
            models_3d: models,
            ..FootprintInstance::default()
        }
    }

    #[test]
    fn empty_pcb_produces_empty_scene() {
        let scene = scene_from_pcb(&Pcb::default());
        assert!(scene.placements.is_empty());
        assert!(scene.board_outline_mm.is_empty());
    }

    #[test]
    fn footprint_without_model_contributes_no_placement() {
        let mut pcb = Pcb::default();
        pcb.footprints
            .push(fp_at("R1", (5.0, 5.0), 0.0, "F.Cu", Vec::new()));
        let scene = scene_from_pcb(&pcb);
        assert!(scene.placements.is_empty());
    }

    #[test]
    fn unrotated_footprint_passes_model_offset_through_unchanged() {
        let model = Model3D {
            path: "x.step".to_string(),
            offset_mm: (1.0, 2.0, 0.5),
            scale: (1.0, 1.0, 1.0),
            rotate_deg: (0.0, 0.0, 0.0),
        };
        let mut pcb = Pcb::default();
        pcb.footprints
            .push(fp_at("U1", (10.0, 20.0), 0.0, "F.Cu", vec![model]));
        let scene = scene_from_pcb(&pcb);
        assert_eq!(scene.placements.len(), 1);
        let p = &scene.placements[0];
        // No footprint rotation → offset adds directly.
        assert!((p.position_mm.0 - 11.0).abs() < 1e-9);
        assert!((p.position_mm.1 - 22.0).abs() < 1e-9);
        assert!((p.position_mm.2 - 0.5).abs() < 1e-9);
        assert_eq!(p.refdes, "U1");
        assert_eq!(p.side, SceneSide::Top);
    }

    #[test]
    fn rotated_footprint_rotates_model_local_xy_into_board_frame() {
        // Footprint at origin, rotated 90° CCW. A model with local
        // offset (1, 0) should appear at world (0, 1).
        let model = Model3D {
            path: "x.step".to_string(),
            offset_mm: (1.0, 0.0, 0.0),
            scale: (1.0, 1.0, 1.0),
            rotate_deg: (0.0, 0.0, 0.0),
        };
        let mut pcb = Pcb::default();
        pcb.footprints
            .push(fp_at("U2", (0.0, 0.0), 90.0, "F.Cu", vec![model]));
        let scene = scene_from_pcb(&pcb);
        let p = &scene.placements[0];
        assert!(
            (p.position_mm.0 - 0.0).abs() < 1e-9,
            "x = {}",
            p.position_mm.0
        );
        assert!(
            (p.position_mm.1 - 1.0).abs() < 1e-9,
            "y = {}",
            p.position_mm.1
        );
        // Footprint's 90° rotation lifts onto the composed rz.
        assert!((p.rotation_deg.2 - 90.0).abs() < 1e-9);
        // X/Y rotations pass through the model unchanged.
        assert!((p.rotation_deg.0 - 0.0).abs() < 1e-9);
        assert!((p.rotation_deg.1 - 0.0).abs() < 1e-9);
    }

    #[test]
    fn bottom_layer_footprint_is_flagged_side_bottom() {
        let model = Model3D::identity("x.step");
        let mut pcb = Pcb::default();
        pcb.footprints
            .push(fp_at("R5", (0.0, 0.0), 0.0, "B.Cu", vec![model]));
        let scene = scene_from_pcb(&pcb);
        assert_eq!(scene.placements[0].side, SceneSide::Bottom);
    }

    #[test]
    fn multi_model_footprint_emits_one_placement_per_model() {
        let model_a = Model3D::identity("a.step");
        let mut model_b = Model3D::identity("b.step");
        model_b.offset_mm = (0.0, 0.0, 1.0);
        let mut pcb = Pcb::default();
        pcb.footprints
            .push(fp_at("U1", (0.0, 0.0), 0.0, "F.Cu", vec![model_a, model_b]));
        let scene = scene_from_pcb(&pcb);
        assert_eq!(scene.placements.len(), 2);
        assert_eq!(scene.placements[0].model_path, "a.step");
        assert_eq!(scene.placements[1].model_path, "b.step");
        assert!((scene.placements[1].position_mm.2 - 1.0).abs() < 1e-9);
    }

    #[test]
    fn scene_inherits_board_thickness_and_outline() {
        let pcb = Pcb {
            thickness_mm: 1.6,
            outline: kiclaude_ki::kcir::Outline {
                points_mm: vec![(0.0, 0.0), (50.0, 0.0), (50.0, 30.0), (0.0, 30.0)],
                ..kiclaude_ki::kcir::Outline::default()
            },
            ..Pcb::default()
        };
        let scene = scene_from_pcb(&pcb);
        assert!((scene.board_thickness_mm - 1.6).abs() < 1e-9);
        assert_eq!(scene.board_outline_mm.len(), 4);
    }
}
