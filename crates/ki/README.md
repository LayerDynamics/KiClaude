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
wasm-pack build --target web crates/ki
```

Both bindings live behind `#[cfg]` gates so a native `cargo build`
pays no cost for either.
