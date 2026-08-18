"""
paths.py
--------
Single source of truth for every filesystem location in the project.

--- WHY THIS EXISTS ----------------------------------------------------------
Paths written relative to the current working directory -- "./data/cache.gpkg"
and the like -- resolve only when the terminal happens to be sitting in the
project root. Invoked from one directory up, from an IDE with a different
working directory, or from a scheduled task, Python looks for those files
somewhere they do not exist.

That failure is worse than a crash, because a missing cache file is
indistinguishable from "no cache yet". Rather than erroring, the pipeline would
quietly start a 45-minute rebuild.

PROJECT_ROOT is therefore derived from THIS FILE's own location on disk, which
is fixed relative to the rest of the repository regardless of where it is
cloned to or invoked from. The working directory stops mattering entirely.
"""

from pathlib import Path

# This file lives at <PROJECT_ROOT>/food_desert/paths.py, so the root is two
# levels up from the file itself. If this module is ever moved, update the
# .parents index to match -- it is the one hardcoded assumption here.
PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent

# --- Inputs ----------------------------------------------------------------
DATA_DIR = PROJECT_ROOT / "data"
ISTAT_DIR = DATA_DIR / "istat"      # committed: small CSVs, part of the method
OSM_DIR = DATA_DIR / "osm"          # gitignored: multi-hundred-MB extracts
CACHE_DIR = DATA_DIR / "cache"      # gitignored: regenerated on demand

# --- Working outputs (gitignored) ------------------------------------------
OUTPUT_DIR = PROJECT_ROOT / "output"

# --- Published site (committed -- this is what GitHub Pages serves) --------
DOCS_DIR = PROJECT_ROOT / "docs"
DOCS_DATA_DIR = DOCS_DIR / "data"

# --- Tools -----------------------------------------------------------------
TOOLS_DIR = PROJECT_ROOT / "tools"


def ensure_directories():
    """
    Creates every directory the pipeline writes into.

    Called once at startup rather than scattered through the modules, so there
    is exactly one place that decides what the working tree looks like.
    """
    for directory in (DATA_DIR, ISTAT_DIR, OSM_DIR, CACHE_DIR,
                      OUTPUT_DIR, DOCS_DIR, DOCS_DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def describe():
    """Prints the resolved layout. Useful when a path problem is suspected."""
    print(f"  PROJECT_ROOT : {PROJECT_ROOT}")
    print(f"  ISTAT data   : {ISTAT_DIR}")
    print(f"  OSM extracts : {OSM_DIR}")
    print(f"  Caches       : {CACHE_DIR}")
    print(f"  Output       : {OUTPUT_DIR}")
    print(f"  Published    : {DOCS_DATA_DIR}")
