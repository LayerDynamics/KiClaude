//! Track routers — pure-Rust geometric path-finding for the PCB editor.
//!
//! At M2 the only router is the **walk-around** variant: A* search on
//! an inflated-obstacle grid that respects clearance but does NOT
//! shove existing tracks aside. Push-and-shove ([SPEC D1] post-M2)
//! is intentionally deferred to M3-R-03.
//!
//! Consumers:
//! - `services/mcp/src/kc_mcp/tools/route.py` — `kc_track_route` calls
//!   into wasm-compiled `kiclaude-cad::routing::walkaround` for real
//!   geometry instead of the Manhattan placeholder.
//! - The React `RouteTool` (M2-T-03) calls the same wasm entry point
//!   live as the user drags the route endpoint.

pub mod diffpair;
pub mod shove;
pub mod walkaround;

pub use diffpair::{route as route_diffpair, DiffPairInput, DiffPairRouteResult};
pub use shove::{ShoveBudget, ShoveItem, ShoveWorld};
pub use walkaround::{route, RoutingError, WalkaroundInput, WalkaroundResult};
