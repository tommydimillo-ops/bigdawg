"""Low-disk detection for the volume Jarvis's persistent stores live on.

Why this exists: this Mac's disk has genuinely hit 0 bytes free during
real work (Phase 9 Reliability S1), and every SQLite write --
`history.db`'s included -- fails with `disk I/O error`/`HistoryBusy` once
that happens. Nothing surfaced the condition to the user before it broke
something; `get_system_status` printed a "Disk:" line but with no
threshold, so 500Mi free looked the same as 500Gi free. This is a signal
only -- it never deletes or cleans anything, on purpose (ROADMAP.md's
"Low-disk health monitoring/alert" item).

Pure and deterministic like `agent/autonomy.py`: a threshold comparison,
never a model call. Thresholds are settings, not constants
(`low_disk_warning_gb`/`low_disk_critical_gb`), because they are
starting values chosen from one Mac's incident history, not tuned.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Optional

from config.settings import settings

_BYTES_PER_GB = 1024 ** 3

# Same directory every persistent store in this project lives under, so
# the number reported is the free space that actually matters to a write.
_DATA_DIR = os.path.expanduser("~/Library/Application Support/CampusPilot")

LEVEL_OK = "ok"
LEVEL_LOW = "low"
LEVEL_CRITICAL = "critical"
LEVEL_UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiskHealth:
    level: str
    free_bytes: Optional[int]
    total_bytes: Optional[int]

    @property
    def free_gb(self) -> Optional[float]:
        return None if self.free_bytes is None else self.free_bytes / _BYTES_PER_GB


def _nearest_existing_dir(path: str) -> str:
    # The data directory may not exist yet on a fresh install; disk_usage
    # of a missing path raises, but the nearest existing ancestor is on
    # the same volume and has the same free space.
    current = os.path.abspath(path)
    while current and not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return current


def check_disk_health(
    path: Optional[str] = None,
    warning_gb: Optional[float] = None,
    critical_gb: Optional[float] = None,
) -> DiskHealth:
    """Classify free space at `path` (default: Jarvis's data directory).

    Never raises: an unreadable volume is `LEVEL_UNKNOWN`, not an error,
    because this runs inside `get_system_status` and the dashboard, and a
    health *check* failing must not break either of them. Defaults are
    resolved inside the body, not bound in the signature, so a redirect
    of `_DATA_DIR` or a settings change is honored (see CLAUDE.md's note
    on definition-time default binding).
    """
    target = _nearest_existing_dir(path if path is not None else _DATA_DIR)
    warning = settings.low_disk_warning_gb if warning_gb is None else warning_gb
    critical = settings.low_disk_critical_gb if critical_gb is None else critical_gb
    try:
        usage = shutil.disk_usage(target)
    except OSError:
        return DiskHealth(LEVEL_UNKNOWN, None, None)

    # `free` here excludes macOS "purgeable" space, so it is the
    # conservative number -- the same one `df` reports and the one a
    # write actually hit ENOSPC against.
    free_gb = usage.free / _BYTES_PER_GB
    if free_gb < critical:
        level = LEVEL_CRITICAL
    elif free_gb < warning:
        level = LEVEL_LOW
    else:
        level = LEVEL_OK
    return DiskHealth(level, usage.free, usage.total)


def format_disk_warning(health: DiskHealth) -> Optional[str]:
    """One plain-language line, or None when there is nothing to warn about.

    Starts with "WARNING:" so `agent/greeting.py`'s prompt block can tell
    the model to relay it without parsing anything else.
    """
    if health.level not in (LEVEL_LOW, LEVEL_CRITICAL) or health.free_gb is None:
        return None
    free = f"{health.free_gb:.1f} GB"
    if health.level == LEVEL_CRITICAL:
        return (
            f"WARNING: disk almost full — only {free} free. Saving history, "
            "memory and other data to disk may start failing; free up space soon."
        )
    return f"WARNING: disk space is getting low — {free} free."
