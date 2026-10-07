#!/usr/bin/env python3
"""Optional entry point for the reference implementation tiled helpers."""
import argparse
import os
from pathlib import Path
import sys


def locate_project(project=None):
    configured = project or os.environ.get("AUTO_PHOTO_ROOT") or os.environ.get("AUTO_PHOTO_PROJECT")
    candidates = ([Path(configured).expanduser().resolve()] if configured else
                  [Path.cwd(), *Path.cwd().parents, *Path(__file__).resolve().parents])
    for candidate in candidates:
        for source_root in (candidate / "reference-code", candidate):
            if (source_root / "auto_photo" / "tiled.py").is_file():
                sys.path.insert(0, str(source_root))
                return
    if configured:
        raise ValueError(f"No optional auto_photo reference code at {configured}")
    # Fall through when an optional copy is already importable.


def main():
    # Preserve the package's subcommand help while accepting a wrapper-only path.
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--project", help="Repository root or reference-code directory")
    args, remaining = parser.parse_known_args()
    try:
        locate_project(args.project)
        from auto_photo.tiled import main as tiled_main
    except ValueError as exc:
        parser.error(str(exc))
    except ModuleNotFoundError as exc:
        if exc.name not in {"auto_photo", "auto_photo.tiled"}:
            raise
        parser.error("Cannot find optional auto_photo reference code; "
                     "run from its repository, pass --project, or set AUTO_PHOTO_ROOT.")
    return tiled_main(remaining)

if __name__ == '__main__':
    raise SystemExit(main())
