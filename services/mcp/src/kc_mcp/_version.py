"""Single source of truth for the kc_mcp package version.

Lives in a leaf module so `server.py` / `tools/ping.py` can import it
without triggering a re-entrant import of `kc_mcp/__init__.py`.
"""

__version__ = "0.1.0"
