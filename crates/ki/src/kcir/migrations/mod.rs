//! KCIR schema migrations.
//!
//! KCIR is versioned (see [`crate::KCIR_VERSION`]). Every release that
//! changes the on-disk shape of a serialized [`Project`](super::Project)
//! must:
//!
//! 1. Bump `KCIR_VERSION` in `crates/ki/src/lib.rs`.
//! 2. Add a migration module here (`v0_2.rs`, `v0_3.rs`, …) with a
//!    `migrate(&mut serde_json::Value)` function that rewrites an
//!    older document in place.
//! 3. Append the migration to [`MIGRATIONS`] in declaration order.
//! 4. Add a smoke test that demonstrates the rewrite works.
//!
//! [`migrate_to_current`] walks every project document from its
//! declared `kcir_version` up to the current crate version, applying
//! each migration in sequence. The M1-Q-02 CI gate uses this to
//! reject PRs that change [`Project`](super::Project) fields without
//! bumping the version + adding a migration.

use semver::Version;
use serde_json::Value;

pub mod v0_2;
pub mod v0_3;
pub mod v0_4;

/// All migrations in order. Each entry says "if the document's
/// `kcir_version` is < `to_version`, run `apply`".
#[allow(clippy::type_complexity)]
pub const MIGRATIONS: &[Migration] = &[
    Migration {
        to_version: "0.2.0",
        apply: v0_2::migrate,
    },
    Migration {
        to_version: "0.3.0",
        apply: v0_3::migrate,
    },
    Migration {
        to_version: "0.4.0",
        apply: v0_4::migrate,
    },
];

/// One migration step: produces a document at `to_version` from any
/// older document.
#[derive(Debug)]
pub struct Migration {
    /// The KCIR version this migration brings the document up to.
    pub to_version: &'static str,
    /// In-place rewrite. The function is responsible for setting
    /// `kcir_version = to_version` on success.
    pub apply: fn(&mut Value),
}

/// Errors [`migrate_to_current`] can return.
#[derive(Debug, thiserror::Error)]
pub enum MigrationError {
    #[error("project JSON has no `kcir_version` field")]
    MissingVersion,
    #[error("project `kcir_version` ({0}) is not a valid semver")]
    InvalidVersion(String),
    #[error(
        "project `kcir_version` ({found}) is newer than this build supports ({current}); \
         upgrade kiclaude-ki"
    )]
    NewerThanCurrent {
        found: String,
        current: &'static str,
    },
}

/// Walk every applicable migration in declaration order and bring
/// `doc` up to [`crate::KCIR_VERSION`].
///
/// # Errors
///
/// Returns [`MigrationError`] if the document doesn't carry a
/// `kcir_version`, the version isn't valid semver, or the version is
/// strictly newer than the current crate version.
///
/// # Panics
///
/// Panics if the in-source `to_version` strings on the entries of
/// [`MIGRATIONS`] aren't valid semver. They're checked at test time
/// so this is a "broken kiclaude build" panic, never a runtime
/// failure from user input.
pub fn migrate_to_current(doc: &mut Value) -> Result<(), MigrationError> {
    let raw = doc
        .get("kcir_version")
        .and_then(|v| v.as_str())
        .ok_or(MigrationError::MissingVersion)?
        .to_string();
    let current_str = crate::KCIR_VERSION;
    let current = Version::parse(current_str)
        .map_err(|_| MigrationError::InvalidVersion(current_str.to_string()))?;
    let mut have = Version::parse(&raw).map_err(|_| MigrationError::InvalidVersion(raw.clone()))?;
    if have > current {
        return Err(MigrationError::NewerThanCurrent {
            found: raw,
            current: current_str,
        });
    }

    for migration in MIGRATIONS {
        let target =
            Version::parse(migration.to_version).expect("MIGRATIONS entries have valid semver");
        if have >= target {
            continue;
        }
        (migration.apply)(doc);
        // The migration is responsible for stamping the new version
        // — if it didn't, we patch it ourselves so an honest mistake
        // doesn't loop forever.
        if doc.get("kcir_version").and_then(|v| v.as_str()) != Some(migration.to_version) {
            if let Value::Object(map) = doc {
                map.insert(
                    "kcir_version".to_string(),
                    Value::String(migration.to_version.to_string()),
                );
            }
        }
        have = target;
    }
    Ok(())
}

#[cfg(test)]
mod tests;
