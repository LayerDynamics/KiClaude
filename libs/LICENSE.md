# License — bundled KiCad library mirror

The symbol, footprint, and 3D-model files under `libs/` are **not** original
to kiclaude. They are a pinned, curated subset of the official KiCad libraries,
redistributed unmodified under their upstream license.

## Source

| Library kind | Upstream repository | Pinned tag |
| ------------ | ------------------- | ---------- |
| Symbols (`libs/symbols/*.kicad_sym`) | <https://gitlab.com/kicad/libraries/kicad-symbols> | `9.0.0` |
| Footprints (`libs/footprints/*.pretty/*.kicad_mod`) | <https://gitlab.com/kicad/libraries/kicad-footprints> | `9.0.0` |

The exact files, their source URLs, and SHA-256 pins are recorded in
[`MANIFEST.toml`](./MANIFEST.toml) and reproduced by `scripts/populate_libs.py`.

## License

The KiCad libraries are licensed under the **Creative Commons CC-BY-SA 4.0**
license, with the following exception granted by the KiCad Library team:

> To the extent that the creation of electronic designs that use "Licensed
> Material" can be considered to be "Adapted Material", then the copyright
> holder waives article 3 of the license with respect to these designs and any
> generated files which use data provided as part of the "Licensed Material".
>
> In other words, there is no requirement for the copyright notice and the
> license to be redistributed for any electronic designs and the files they
> generate (e.g. Gerber files) that use these libraries.

- Full license text: <https://creativecommons.org/licenses/by-sa/4.0/legalcode>
- Upstream license statement: <https://gitlab.com/kicad/libraries/kicad-symbols/-/blob/9.0.0/LICENSE.md>

## Attribution

© KiCad Libraries Team and contributors, licensed CC-BY-SA 4.0 with the KiCad
Library Exception. kiclaude redistributes these files unmodified; any changes
to a project's design files that *use* these libraries are unencumbered by the
share-alike clause per the exception above.
