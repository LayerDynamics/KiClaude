# kiclaude-ki

Rust crate for the kiclaude Canonical IR (KCIR), `KiCad` file parsers /
emitters, and the `ki_native` Python extension module.

## Usage from Rust

```rust
use kiclaude_ki::format::KiProject;

let project = KiProject::open("examples/blinky")?;
println!("{}", project.project.name);
```

## Build the `ki_native` Python module

```bash
# from this directory:
maturin develop --features python
python -c "from ki_native import open_project; print(open_project('../../examples/blinky'))"
```

## Build the WebAssembly package

```bash
# from the repo root:
wasm-pack build --target web crates/ki -- --features wasm-api
```

`wasm-api` is off by default so this crate can be pulled in as a Rust
dependency from other wasm-pack-built crates (e.g. `kiclaude-cad`'s
KCIR-aware solvers) without colliding with their `#[wasm_bindgen]`
exports.

Both bindings live behind `#[cfg]` gates so a native `cargo build`
pays no cost for either.
