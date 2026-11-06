"""Locate the skill under test, wherever this bundle was unzipped.

The tests ship inside the bundle as `tests/`, next to `scripts/`. Hard-coding
an absolute path made them unrunnable on any other machine, which is exactly
the "cannot reproduce" objection this bundle answers.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def _find_scripts() -> str:
    for candidate in (os.path.join(ROOT, "scripts"),
                      os.path.join(HERE, "scripts")):
        p = os.path.abspath(candidate)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "_common.py")):
            return p
    raise SystemExit(f"cannot find scripts/ from {HERE}")


SCRIPTS = _find_scripts()
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)
os.chdir(SCRIPTS)

# generated media goes here, beside the bundle, never inside scripts/
OUTDIR = os.path.join(HERE, "_out")
os.makedirs(OUTDIR, exist_ok=True)


def out(*parts: str) -> str:
    return os.path.join(OUTDIR, *parts)
