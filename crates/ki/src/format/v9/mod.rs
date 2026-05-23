//! `KiCad` 9 format mappers.
//!
//! Reads `.kicad_pro` (JSON) + `.kicad_pcb` (S-expression) from a project
//! directory and maps both into [`kcir::Project`](crate::kcir::Project).
//! M0-R-05 scope: project name, one footprint, one track, one zone, one
//! net round-trip cleanly. Wider field coverage follows in M1/M2.

pub mod emit;
pub mod pcb;
pub mod project;
pub mod sch;
pub mod sch_emit;
pub mod sexpr_helpers;
pub mod symbols;

pub use emit::{emit_pcb, emit_pcb_with_stackup};
pub use sch::{map_sch, merge_into_schematic, ParsedSheet};
pub use sch_emit::{emit_sch, emit_sch_canonical, emit_sch_with_edits, span_key, EditedSpans};

use std::fs;
use std::path::{Path, PathBuf};

use thiserror::Error;

use crate::kcir;
use crate::sexpr::{parse_str, ParseError};

/// A `KiCad` project opened from disk.
#[derive(Debug, Clone)]
pub struct KiProject {
    /// Directory the project was opened from.
    pub root: PathBuf,
    /// Path to the `.kicad_pro` file.
    pub pro_path: PathBuf,
    /// Path to the `.kicad_pcb` file (may not yet exist for a fresh project).
    pub pcb_path: Option<PathBuf>,
    /// Path to the root `.kicad_sch` file (may not exist for a
    /// PCB-only project).
    pub sch_path: Option<PathBuf>,
    /// Original `.kicad_sch` source text retained so
    /// [`save_sch`](Self::save_sch) can emit byte-identical output for
    /// unmodified forms (M1-R-02).
    pub sch_source: Option<String>,
    /// Parsed `.kicad_sch` root S-expression. Paired with [`sch_source`](Self::sch_source).
    pub sch_root: Option<crate::sexpr::SNode>,
    /// Per-sheet parser output. Populated by
    /// [`KiProject::open`](Self::open) when a `.kicad_sch` is present.
    pub parsed_sheet: Option<sch::ParsedSheet>,
    /// The KCIR view of the project.
    pub project: kcir::Project,
}

/// Errors [`KiProject::open`] can return.
#[derive(Debug, Error)]
pub enum OpenError {
    #[error("I/O error reading {path}: {source}")]
    Io {
        path: PathBuf,
        #[source]
        source: std::io::Error,
    },
    #[error("project directory {0} does not exist or is not a directory")]
    NotADir(PathBuf),
    #[error("no `.kicad_pro` file found in {0}")]
    NoProjectFile(PathBuf),
    #[error("multiple `.kicad_pro` files in {dir}: {names:?}")]
    MultipleProjectFiles { dir: PathBuf, names: Vec<String> },
    #[error("invalid `.kicad_pro` JSON in {path}: {source}")]
    InvalidProjectJson {
        path: PathBuf,
        #[source]
        source: serde_json::Error,
    },
    #[error("invalid `.kicad_pcb` S-expression in {path}: {source}")]
    InvalidPcbSexpr {
        path: PathBuf,
        #[source]
        source: ParseError,
    },
    #[error("`.kicad_pcb` in {path} has no top-level form")]
    EmptyPcb { path: PathBuf },
    #[error("`.kicad_pcb` in {path} top-level form is not `(kicad_pcb …)`")]
    NotKicadPcb { path: PathBuf },
    #[error("invalid `.kicad_sch` S-expression in {path}: {source}")]
    InvalidSchSexpr {
        path: PathBuf,
        #[source]
        source: ParseError,
    },
    #[error("`.kicad_sch` in {path} has no top-level form")]
    EmptySch { path: PathBuf },
    #[error("`.kicad_sch` in {path} top-level form is not `(kicad_sch …)`")]
    NotKicadSch { path: PathBuf },
    #[error("mapping error in {path}: {message}")]
    Mapping { path: PathBuf, message: String },
}

impl KiProject {
    /// Open a `KiCad` project from a directory.
    ///
    /// The directory must contain exactly one `.kicad_pro` file. The
    /// matching `<stem>.kicad_pcb` is read if present.
    ///
    /// # Errors
    /// Returns [`OpenError`] on I/O failure, malformed JSON / S-expression,
    /// or mapping mismatches.
    pub fn open(dir: impl AsRef<Path>) -> Result<Self, OpenError> {
        let dir = dir.as_ref();
        if !dir.is_dir() {
            return Err(OpenError::NotADir(dir.to_path_buf()));
        }
        let pro_path = find_unique_project_file(dir)?;
        let stem = pro_path
            .file_stem()
            .and_then(std::ffi::OsStr::to_str)
            .ok_or_else(|| OpenError::Mapping {
                path: pro_path.clone(),
                message: "project file stem is not valid UTF-8".to_string(),
            })?
            .to_string();

        let pro_text = fs::read_to_string(&pro_path).map_err(|source| OpenError::Io {
            path: pro_path.clone(),
            source,
        })?;
        let pro_doc: project::ProjectDoc =
            serde_json::from_str(&pro_text).map_err(|source| OpenError::InvalidProjectJson {
                path: pro_path.clone(),
                source,
            })?;

        let mut kproject = kcir::Project::default();
        project::apply_project_doc(&pro_doc, &stem, &mut kproject);

        let pcb_path = load_pcb_if_present(dir, &stem, &mut kproject)?;
        let sch_load = load_sch_if_present(dir, &stem, &mut kproject)?;

        Ok(Self {
            root: dir.to_path_buf(),
            pro_path,
            pcb_path,
            sch_path: sch_load.as_ref().map(|l| l.path.clone()),
            sch_source: sch_load.as_ref().map(|l| l.source.clone()),
            sch_root: sch_load.as_ref().map(|l| l.root.clone()),
            parsed_sheet: sch_load.map(|l| l.parsed),
            project: kproject,
        })
    }

    /// Write the project's `.kicad_sch` back to disk.
    ///
    /// When the project was opened from an existing `.kicad_sch`, the
    /// original source text is replayed byte-identically so unmodified
    /// forms preserve their KiCad-IDE formatting (M1-R-02). When no
    /// source was retained (e.g. a fresh project), a canonical
    /// re-serialization is produced from KCIR via
    /// [`sch_emit::emit_sch_canonical`].
    ///
    /// `edited_spans` lets editing flows mark which top-level forms in
    /// the parsed tree should be re-canonicalized; everything else
    /// keeps its original bytes. Pass an empty set for an unmodified
    /// round-trip save.
    ///
    /// # Errors
    /// Returns [`OpenError::Io`] on write failure or
    /// [`OpenError::Mapping`] if the project has no schematic at all
    /// (neither parsed root nor `.kicad_pro` stem).
    pub fn save_sch(&self, edited_spans: &sch_emit::EditedSpans) -> Result<PathBuf, OpenError> {
        let target = self.resolve_sch_path()?;
        let text = self.render_sch(edited_spans)?;
        fs::write(&target, text).map_err(|source| OpenError::Io {
            path: target.clone(),
            source,
        })?;
        Ok(target)
    }

    fn resolve_sch_path(&self) -> Result<PathBuf, OpenError> {
        if let Some(p) = &self.sch_path {
            return Ok(p.clone());
        }
        let stem = self
            .pro_path
            .file_stem()
            .and_then(std::ffi::OsStr::to_str)
            .ok_or_else(|| OpenError::Mapping {
                path: self.pro_path.clone(),
                message: "project file stem is not valid UTF-8".to_string(),
            })?;
        Ok(self.root.join(format!("{stem}.kicad_sch")))
    }

    fn render_sch(&self, edited_spans: &sch_emit::EditedSpans) -> Result<String, OpenError> {
        if let (Some(root), Some(source), Some(parsed)) =
            (&self.sch_root, &self.sch_source, &self.parsed_sheet)
        {
            if edited_spans.is_empty() {
                return Ok(sch_emit::emit_sch(root, source));
            }
            return sch_emit::emit_sch_with_edits(root, source, parsed, edited_spans).map_err(
                |message| OpenError::Mapping {
                    path: self.sch_path.clone().unwrap_or_else(|| self.root.clone()),
                    message,
                },
            );
        }
        let parsed = self.parsed_sheet.clone().unwrap_or_default();
        Ok(sch_emit::emit_sch_canonical(&parsed))
    }

    /// Write the project's `.kicad_pcb` back to disk in canonical form.
    ///
    /// If [`pcb_path`](Self::pcb_path) is `None`, derives the path from
    /// the `.kicad_pro` stem (so a freshly-opened project with no PCB
    /// file still gets one written here).
    ///
    /// # Errors
    /// Returns [`OpenError::Io`] on write failure or
    /// [`OpenError::Mapping`] if the `.kicad_pro` stem is not valid UTF-8.
    pub fn save_pcb(&self) -> Result<PathBuf, OpenError> {
        // Only emit a `(stackup …)` block when the project carries one
        // distinct from the bare in-memory default — otherwise an
        // existing M0 fixture without a stackup would round-trip with
        // a spurious 2-layer-FR4 block injected.
        let stackup_default = crate::kcir::Stackup::default();
        let stackup = if self.project.stackup == stackup_default {
            None
        } else {
            Some(&self.project.stackup)
        };
        let text = emit::emit_pcb_with_stackup(&self.project.pcb, stackup);
        let target = if let Some(p) = &self.pcb_path {
            p.clone()
        } else {
            let stem = self
                .pro_path
                .file_stem()
                .and_then(std::ffi::OsStr::to_str)
                .ok_or_else(|| OpenError::Mapping {
                    path: self.pro_path.clone(),
                    message: "project file stem is not valid UTF-8".to_string(),
                })?;
            self.root.join(format!("{stem}.kicad_pcb"))
        };
        fs::write(&target, text).map_err(|source| OpenError::Io {
            path: target.clone(),
            source,
        })?;
        Ok(target)
    }
}

fn find_unique_project_file(dir: &Path) -> Result<PathBuf, OpenError> {
    let mut found: Vec<PathBuf> = Vec::new();
    let read_dir = fs::read_dir(dir).map_err(|source| OpenError::Io {
        path: dir.to_path_buf(),
        source,
    })?;
    for entry in read_dir {
        let entry = entry.map_err(|source| OpenError::Io {
            path: dir.to_path_buf(),
            source,
        })?;
        let path = entry.path();
        if path.extension().and_then(std::ffi::OsStr::to_str) == Some("kicad_pro") && path.is_file()
        {
            found.push(path);
        }
    }
    match found.len() {
        0 => Err(OpenError::NoProjectFile(dir.to_path_buf())),
        1 => Ok(found.into_iter().next().expect("len() == 1")),
        _ => {
            let mut names: Vec<String> = found
                .iter()
                .filter_map(|p| p.file_name().and_then(|n| n.to_str()).map(String::from))
                .collect();
            names.sort();
            Err(OpenError::MultipleProjectFiles {
                dir: dir.to_path_buf(),
                names,
            })
        }
    }
}

/// Load `<dir>/<stem>.kicad_pcb` into `kproject.pcb` when it exists.
/// Returns the file path it loaded from, or `None` if absent.
fn load_pcb_if_present(
    dir: &Path,
    stem: &str,
    kproject: &mut kcir::Project,
) -> Result<Option<PathBuf>, OpenError> {
    let candidate = dir.join(format!("{stem}.kicad_pcb"));
    if !candidate.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&candidate).map_err(|source| OpenError::Io {
        path: candidate.clone(),
        source,
    })?;
    let nodes = parse_str(&text).map_err(|source| OpenError::InvalidPcbSexpr {
        path: candidate.clone(),
        source,
    })?;
    let root = nodes.first().ok_or_else(|| OpenError::EmptyPcb {
        path: candidate.clone(),
    })?;
    if root.head_symbol() != Some("kicad_pcb") {
        return Err(OpenError::NotKicadPcb {
            path: candidate.clone(),
        });
    }
    kproject.pcb = pcb::map_pcb(root).map_err(|message| OpenError::Mapping {
        path: candidate.clone(),
        message,
    })?;
    // M3-R-01: pull `(setup (stackup ...))` off the .kicad_pcb root
    // and lift it onto the project-level KCIR field. KiCad stores
    // the stackup on the board file even though our KCIR model puts
    // it on `Project` (it's shared with the schematic / fab side).
    if let Some(stackup) = pcb::map_stackup_from_pcb(root) {
        kproject.stackup = stackup;
    }
    Ok(Some(candidate))
}

/// Artifacts produced by [`load_sch_if_present`] when a `.kicad_sch`
/// is found on disk. Returned wholesale so [`KiProject::open`] can
/// retain the original text + parse tree + KCIR view together.
struct LoadedSch {
    path: PathBuf,
    source: String,
    root: crate::sexpr::SNode,
    parsed: sch::ParsedSheet,
}

/// Load `<dir>/<stem>.kicad_sch` and merge it into `kproject.schematic`
/// when it exists. Patches (or inserts) the matching root-sheet entry
/// with the on-sheet uuid + layout.
fn load_sch_if_present(
    dir: &Path,
    stem: &str,
    kproject: &mut kcir::Project,
) -> Result<Option<LoadedSch>, OpenError> {
    let candidate = dir.join(format!("{stem}.kicad_sch"));
    if !candidate.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&candidate).map_err(|source| OpenError::Io {
        path: candidate.clone(),
        source,
    })?;
    let nodes = parse_str(&text).map_err(|source| OpenError::InvalidSchSexpr {
        path: candidate.clone(),
        source,
    })?;
    let root = nodes.first().ok_or_else(|| OpenError::EmptySch {
        path: candidate.clone(),
    })?;
    if root.head_symbol() != Some("kicad_sch") {
        return Err(OpenError::NotKicadSch {
            path: candidate.clone(),
        });
    }
    let parsed = sch::map_sch(root).map_err(|message| OpenError::Mapping {
        path: candidate.clone(),
        message,
    })?;
    // The project-doc parser seeded `schematic.sheets` from
    // `top_level_sheets`. Patch the matching seed (or insert a new
    // sheet) with the on-sheet uuid + layout from the `.kicad_sch`.
    let expected_file = format!("{stem}.kicad_sch");
    let seed = kproject
        .schematic
        .sheets
        .iter()
        .find(|s| s.file == expected_file || s.file.is_empty() || s.name == stem)
        .cloned()
        .or_else(|| {
            Some(kcir::Sheet {
                name: stem.to_owned(),
                file: expected_file,
                ..kcir::Sheet::default()
            })
        });
    sch::merge_into_schematic(parsed.clone(), &mut kproject.schematic, seed);
    Ok(Some(LoadedSch {
        path: candidate,
        source: text,
        root: root.clone(),
        parsed,
    }))
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod sch_tests;

#[cfg(test)]
mod sch_emit_tests;
