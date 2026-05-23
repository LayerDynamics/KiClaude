"""Dump the FastAPI OpenAPI schema to a JSON file.

The GUI's ``gen:types`` step calls this to generate TypeScript types from
the backend's API contract — so codegen needs no running server. Exposed
as the ``ki-mcp-pcb-openapi`` console script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ki_mcp_pcb_web.server import app


def dump_openapi(out_path: Path | str) -> Path:
    """Write the app's OpenAPI schema as indented JSON; return the path."""
    path = Path(out_path)
    path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    """Console entry point — ``ki-mcp-pcb-openapi [out.json]``."""
    out = sys.argv[1] if len(sys.argv) > 1 else "openapi.json"
    written = dump_openapi(out)
    print(f"wrote OpenAPI schema to {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
