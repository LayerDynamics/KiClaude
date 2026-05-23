"""Backend adapters.

v1 ships only the KiCad backend. The interface exists so future
adapters (Horizon EDA, LibrePCB) can be added without leaking
backend-specific code into the higher layers.
"""

from ki_mcp_pcb_core.backends.base import Backend

__all__ = ["Backend"]
