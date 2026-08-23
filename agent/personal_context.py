"""Privacy-preserving local inventory for personal context.

This module deliberately catalogs aggregates, not file contents or raw file
names. It never calls an AI provider. Photos are summarized from the Photos
SQLite database in read-only mode; image pixels, face data, and coordinates
are not read or persisted.
"""
import json
import os
import re
import sqlite3
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Iterable, Optional


CATALOG_FILE = os.path.expanduser(
    "~/Library/Application Support/CampusPilot/personal_context.json"
)
DEFAULT_ROOTS = ("~/Desktop", "~/Documents", "~/Downloads")
DEFAULT_PHOTOS_DB = (
    "~/Pictures/Photos Library.photoslibrary/database/Photos.sqlite"
)

_SKIP_DIRS = {
    ".git", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    "build", "dist", ".cache", "DerivedData",
}
_SENSITIVE_NAME = re.compile(
    r"(^|[._ -])(password|passwd|secret|token|credential|api[_ -]?key)([._ -]|$)",
    re.IGNORECASE,
)
_COURSE_CODE = re.compile(r"\b([A-Z]{2,4})[ _-]?(\d{3})\b", re.IGNORECASE)


def _safe_extension(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix and len(suffix) <= 12 else "[none]"


def _walk_files(roots: Iterable[str]):
    for root_value in roots:
        root = Path(os.path.expanduser(root_value)).resolve()
        if not root.is_dir():
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [
                name for name in dirs
                if name not in _SKIP_DIRS and not name.startswith(".")
                and not name.endswith((".app", ".photoslibrary"))
            ]
            for name in files:
                if name.startswith(".") or name.startswith("~$"):
                    continue
                if _SENSITIVE_NAME.search(name):
                    continue
                yield root, Path(current) / name


def _photo_summary(photo_db: str) -> dict:
    path = Path(os.path.expanduser(photo_db))
    if not path.is_file():
        return {"available": False}

    # URI read-only mode prevents accidental writes, including SQLite
    # journals, to the user's Photos library.
    uri = f"file:{path}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=2) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*), MIN(ZDATECREATED), MAX(ZDATECREATED),
                       SUM(CASE WHEN ZFAVORITE = 1 THEN 1 ELSE 0 END)
                FROM ZASSET WHERE ZTRASHEDSTATE = 0
                """
            ).fetchone()
    except (sqlite3.Error, OSError):
        return {"available": False}

    def _year(value):
        # Core Data timestamps use 2001-01-01 as their epoch.
        return time.gmtime(float(value) + 978307200).tm_year if value is not None else None

    return {
        "available": True,
        "asset_count": int(row[0] or 0),
        "earliest_year": _year(row[1]),
        "latest_year": _year(row[2]),
        "favorite_count": int(row[3] or 0),
    }


def build_catalog(
    roots: Optional[Iterable[str]] = None,
    photo_db: str = DEFAULT_PHOTOS_DB,
) -> dict:
    """Build an aggregate-only catalog without reading document contents."""
    selected_roots = tuple(roots or DEFAULT_ROOTS)
    extensions = Counter()
    root_counts = Counter()
    course_codes = Counter()
    newest_mtime = None

    for root, path in _walk_files(selected_roots):
        extensions[_safe_extension(path)] += 1
        root_counts[root.name] += 1
        try:
            modified = path.stat().st_mtime
            newest_mtime = max(newest_mtime or modified, modified)
        except OSError:
            pass
        for subject, number in _COURSE_CODE.findall(path.stem):
            course_codes[f"{subject.upper()} {number}"] += 1

    return {
        "version": 1,
        "generated_at": time.time(),
        "roots": dict(sorted(root_counts.items())),
        "total_files": sum(root_counts.values()),
        "extensions": dict(extensions.most_common(30)),
        "recurring_course_codes": [
            {"code": code, "mentions": count}
            for code, count in course_codes.most_common(12)
            if count >= 2
        ],
        "newest_file_mtime": newest_mtime,
        "photos": _photo_summary(photo_db),
        "privacy": {
            "document_contents_stored": False,
            "filenames_stored": False,
            "photo_pixels_stored": False,
            "photo_locations_stored": False,
            "cloud_analysis_used": False,
        },
    }


def save_catalog(catalog: dict, path: Optional[str] = None) -> None:
    destination = os.path.expanduser(path if path is not None else CATALOG_FILE)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix="personal-context-", suffix=".tmp", dir=os.path.dirname(destination)
    )
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(catalog, file, indent=2, sort_keys=True)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def refresh_catalog(roots: Optional[Iterable[str]] = None) -> dict:
    catalog = build_catalog(roots=roots)
    save_catalog(catalog)
    return catalog


def load_catalog(path: Optional[str] = None) -> Optional[dict]:
    try:
        with open(os.path.expanduser(path if path is not None else CATALOG_FILE)) as file:
            value = json.load(file)
        return value if isinstance(value, dict) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def summary_text(path: Optional[str] = None) -> str:
    """Human/model-readable aggregate summary with no raw paths or names."""
    catalog = load_catalog(path or CATALOG_FILE)
    if not catalog:
        return "No local personal-context catalog has been created yet."

    roots = ", ".join(
        f"{name}: {count} files" for name, count in catalog.get("roots", {}).items()
    ) or "no document roots"
    courses = ", ".join(
        item["code"] for item in catalog.get("recurring_course_codes", [])
    ) or "none detected"
    photos = catalog.get("photos", {})
    photo_text = "photo library unavailable"
    if photos.get("available"):
        photo_text = (
            f"{photos.get('asset_count', 0)} photo/video assets spanning "
            f"{photos.get('earliest_year')}–{photos.get('latest_year')}"
        )
    return (
        f"Local metadata catalog: {catalog.get('total_files', 0)} files "
        f"({roots}). Recurring course codes: {courses}. Photos: {photo_text}. "
        "This catalog contains aggregates only; no filenames, contents, pixels, "
        "faces, or locations are stored."
    )
