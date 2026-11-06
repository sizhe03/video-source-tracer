"""Run everything: compile, encoding scan, all suites. Offline.

    python tests/run_all.py

Exit code 0 means every gate passed. Nothing here needs the network, media, or
a particular install path.
"""
import glob
import os
import py_compile
import subprocess
import sys

import _paths
from _paths import SCRIPTS, HERE

SKILL = os.path.dirname(SCRIPTS)
print("skill under test:", SKILL)

print("=" * 84)
print("1. compile every module")
print("=" * 84)
bad = []
for f in sorted(glob.glob(os.path.join(SCRIPTS, "*.py"))):
    try:
        py_compile.compile(f, doraise=True, cfile=None)
        print(f"  OK   {os.path.basename(f)}")
    except Exception as e:
        bad.append((os.path.basename(f), str(e)))
        print(f"  FAIL {os.path.basename(f)}: {e}")

print()
print("=" * 84)
print("2. encoding scan (mojibake / BOM / invalid UTF-8)")
print("=" * 84)
MOJ = "\u9225\u951b\u9286\u9422\u9359\u93b4\u6d93\u93c4"
files = [p for p in glob.glob(os.path.join(SKILL, "**", "*"), recursive=True)
         if os.path.isfile(p) and not p.endswith(".pyc")
         and "__pycache__" not in p and os.sep + "_out" + os.sep not in p]
faults = []
for f in files:
    raw = open(f, "rb").read()
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        faults.append((os.path.relpath(f, SKILL), f"invalid utf-8 at {e.start}"))
        continue
    if any(c in txt for c in MOJ):
        faults.append((os.path.relpath(f, SKILL), "mojibake"))
    if raw[:3] == b"\xef\xbb\xbf":
        faults.append((os.path.relpath(f, SKILL), "BOM"))
    if "\ufffd" in txt:
        faults.append((os.path.relpath(f, SKILL), "replacement char"))
print(f"  {len(files)} files scanned, {len(faults)} fault(s)")
for name, why in faults:
    print(f"    {name}: {why}")

print()
print("=" * 84)
print("3. suites")
print("=" * 84)
suites = ["regress_auprint.py", "regress_find_earlier.py",
          "regress_v5_counterexamples.py", "regress_v6_counterexamples.py", "regress_v7_counterexamples.py",
          "entry_e2e.py", "reverify.py", "regress_discover.py", "regress_v8_fixes.py",
          "regress_review_findings.py", "regress_trace.py"]
env = dict(os.environ, PYTHONIOENCODING="utf-8")
results = []
for s in suites:
    p = subprocess.run([sys.executable, os.path.join(HERE, s)], cwd=HERE,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    lines = [l for l in (p.stdout or "").strip().splitlines() if l.strip()]
    summary = lines[-1] if lines else "(no output)"
    results.append((s, p.returncode, summary))
    print(f"  {s:28s} exit={p.returncode}  {summary}")
    if p.returncode != 0:
        for l in lines[-8:]:
            print(f"      {l}")

print()
print("=" * 84)
print("SUMMARY")
print("=" * 84)
fails = [r for r in results if r[1] != 0]
print(f"  compile errors : {len(bad)}")
print(f"  encoding faults: {len(faults)}")
print(f"  suites failing : {len(fails)} / {len(results)}")
sys.exit(1 if (bad or faults or fails) else 0)
