# Small static verification helper: prefix order, suite arithmetic, sections 11-15.
import pathlib, sys
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import trace as T

p = T._NAME_PREFIX.pattern
alts = p[p.index("(?:") + 3:p.index(")+")].split("|")
print("prefix_alternatives_head =", alts[:8])
print("longest_before_short =", alts.index("应该是这个") < alts.index("应该是"))
print("comment_names('应该是这个桑塔阿普亚') =",
      T._comment_names("应该是这个桑塔阿普亚"))
counts = [7, 4, 13, 23, 32, 9, 14, 33, 47, 26, 66]
print("suite_counts =", counts)
print("suite_total =", sum(counts), "suites =", len(counts))
src = (HERE / "regress_trace.py").read_text(encoding="utf-8")
for line in src.splitlines():
    if line.startswith('print("-- 1') or line.startswith('print("-- 2'):
        print("section:", line)
print("sections_11_15_present =",
      all(("-- %d." % n) in src for n in range(11, 16)))
