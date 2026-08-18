#!/usr/bin/env python3
"""
run.py
------
Single entry point for the Food Desert Analysis project.

    python run.py analyze              Run the full analysis pipeline
    python run.py layers               Build the map overlay layers
    python run.py publish              Copy results into docs/ for the live demo
    python run.py serve                Preview the web map locally
    python run.py diagnose "Town Name" Spot-check one or more towns
    python run.py paths                Show where the project expects its files
    python run.py all                  analyze -> layers -> publish

--- WHY A SINGLE ENTRY POINT -------------------------------------------------
  * The pipeline's shape is visible in one place. `python run.py --help` is a
    truthful description of what this project does, rather than an ordering
    that exists only in the README.
  * The working directory stops mattering. Combined with paths.py, this can be
    invoked from anywhere and still resolve everything from the repository root.
  * Publishing to the live demo is an explicit, named action rather than a side
    effect of running the analysis.
"""

import argparse
import shutil
import subprocess
import sys
import webbrowser

from food_desert import config, paths


def cmd_analyze(_args):
    """Runs the full analysis and writes results to output/."""
    from food_desert.pipeline import run_pipeline
    run_pipeline()


def cmd_layers(_args):
    """Builds the cycling-lane and public-transport overlay layers."""
    from food_desert.fetch_map_layers import (
        fetch_cycling_lanes_geojson,
        fetch_public_transport_geojson,
    )
    paths.ensure_directories()
    fetch_cycling_lanes_geojson()
    fetch_public_transport_geojson()


def cmd_publish(_args):
    """
    Copies the GeoJSON outputs into docs/data/, where GitHub Pages serves them.

    This is deliberately a separate command rather than something `analyze`
    does automatically. The published files ARE the live demo -- making the
    copy explicit means an experimental run, a partial run, or a run with a
    changed threshold cannot silently replace what the world sees.
    """
    paths.ensure_directories()

    missing = [p for p in config.PUBLISHABLE_FILES if not p.exists()]
    if missing:
        print("[ERROR] Cannot publish -- these files have not been generated yet:")
        for p in missing:
            print(f"          {p.name}")
        print("\n        Run:  python run.py analyze     (towns + routes)")
        print("              python run.py layers      (cycling + transport)")
        return 1

    total_mb = 0.0
    print(f"[INFO] Publishing to {paths.DOCS_DATA_DIR}")
    for src in config.PUBLISHABLE_FILES:
        dst = paths.DOCS_DATA_DIR / src.name
        shutil.copy2(src, dst)
        size_mb = dst.stat().st_size / (1024 * 1024)
        total_mb += size_mb
        print(f"       {src.name:28} {size_mb:6.2f} MB")

    print(f"[INFO] Published {len(config.PUBLISHABLE_FILES)} files, {total_mb:.2f} MB total.")

    # GitHub warns above 50MB per file and recommends repos stay under 1GB.
    # More practically: every megabyte here is a megabyte each visitor
    # downloads before the map draws anything.
    if total_mb > 25:
        print(f"[WARN] {total_mb:.0f} MB is a heavy first load for a web map. "
              f"Consider simplifying route geometry or splitting the cycling "
              f"layer by zoom level.")

    print("\n       Next:  git add docs/ && git commit -m 'Update published data'")
    return 0


def cmd_serve(args):
    """Serves docs/ locally so the map can be previewed exactly as published."""
    url = f"http://localhost:{args.port}/"
    print(f"[INFO] Serving {paths.DOCS_DIR} at {url}")
    print("[INFO] This mirrors how GitHub Pages will serve it. Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)

    # Ctrl+C is the normal, documented way to stop a dev server -- it is not an
    # error, and it should not print a stack trace. subprocess.run() propagates
    # KeyboardInterrupt from the child, so it is caught and reported plainly.
    try:
        subprocess.run(
            [sys.executable, "-m", "http.server", str(args.port)],
            cwd=paths.DOCS_DIR,
        )
    except KeyboardInterrupt:
        print("\n[INFO] Server stopped.")
    return 0


def cmd_diagnose(args):
    """Spot-checks specific towns against cached and live data."""
    from food_desert.diagnostics import run_diagnostics
    run_diagnostics(args.towns)


def cmd_paths(_args):
    """Prints the resolved project layout and flags anything missing."""
    print("Resolved project layout:")
    paths.describe()

    print("\nKey inputs:")
    checks = [
        ("OSM extract", config.OSM_PBF_PATH),
        ("Towns cache", config.TOWNS_CACHE_PATH),
        ("Supermarket cache", config.SUPERMARKETS_CACHE_PATH),
        *[(f"ISTAT {p.stem[:24]}", p) for p in config.ISTAT_POPULATION_XLSX],
    ]
    for label, path in checks:
        mark = "OK     " if path.exists() else "MISSING"
        print(f"  [{mark}] {label:22} {path}")


def cmd_all(args):
    """analyze -> layers -> publish, in the correct order."""
    cmd_analyze(args)
    cmd_layers(args)
    return cmd_publish(args)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Food Desert Analysis -- northeastern Italy.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("analyze", help="Run the full analysis pipeline").set_defaults(func=cmd_analyze)
    sub.add_parser("layers", help="Build map overlay layers").set_defaults(func=cmd_layers)
    sub.add_parser("publish", help="Copy results into docs/ for the live demo").set_defaults(func=cmd_publish)
    sub.add_parser("paths", help="Show resolved paths and check inputs exist").set_defaults(func=cmd_paths)
    sub.add_parser("all", help="analyze, then layers, then publish").set_defaults(func=cmd_all)

    p_serve = sub.add_parser("serve", help="Preview the web map locally")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--no-browser", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_diag = sub.add_parser("diagnose", help="Spot-check specific towns")
    p_diag.add_argument("towns", nargs="+", metavar="TOWN",
                        help='One or more town names, e.g. "Torri di Quartesolo"')
    p_diag.set_defaults(func=cmd_diagnose)

    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    sys.exit(args.func(args) or 0)
