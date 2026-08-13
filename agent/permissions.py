"""Backward-compatible wrapper -- permission levels are now defined once,
alongside each tool's schema and handler, in tools/registry.py (each
ToolSpec's permission_level; see that module for the 6-level model this
still exactly preserves). This module just re-exports the same lookup
functions so existing callers (agent/audit.py, pages/1_Dashboard.py,
tests/test_safety.py) don't need to change."""
from tools.registry import (
    LEVEL_NAMES,
    check_full_coverage,
    permission_label,
    permission_level,
)

__all__ = ["LEVEL_NAMES", "check_full_coverage", "permission_label", "permission_level"]
