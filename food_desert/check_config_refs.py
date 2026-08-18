#!/usr/bin/env python3
"""
check_config_refs.py
--------------------
Static check: verifies every `config.SOMETHING` referenced anywhere in the
package actually exists in config.py.

Renaming a config setting is silently safe until runtime, and the failure can
surface a long way in: a stale reference inside a loop that only begins after a
multi-minute file read will not raise until several minutes into a run. Ten
seconds of static checking catches it before the process starts.

    python tools/check_config_refs.py

Exits non-zero on failure, so it works as a pre-commit hook or CI step.
"""

import ast
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = PROJECT_ROOT / "food_desert"


def defined_settings():
    tree = ast.parse((PACKAGE_DIR / "config.py").read_text(encoding="utf-8"))
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def main():
    defined = defined_settings()
    problems = []

    sources = list(PACKAGE_DIR.glob("*.py")) + [PROJECT_ROOT / "run.py"]
    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "config"
                    and node.attr not in defined):
                problems.append((source.name, node.lineno, node.attr))

    if problems:
        print(f"FAIL -- {len(problems)} reference(s) to settings not in config.py:")
        for filename, line, attr in problems:
            print(f"   {filename}:{line}  config.{attr}")
        return 1

    print(f"OK -- all config references resolve ({len(defined)} settings defined, "
          f"{len(sources)} files checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
