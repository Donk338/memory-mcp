#!/usr/bin/env python3
"""Catalog auditor — proves every significant directory carries the etiquette.

Walks the repo, reads .registry.yml markers (with inheritance), and reports
coverage, gaps, stale stamps, and malformed markers. One command to audit the
whole estate. See memory/registry/TAXONOMY.md for the marker spec + taxonomy.

Exit 0 = healthy (coverage >= threshold, no stale/malformed).
Exit 1 = gaps to close (the improvement-session's findings).
"""
import os, sys, json, datetime, re

def _root():
    for i, a in enumerate(sys.argv):
        if a == "--root" and i+1 < len(sys.argv): return os.path.abspath(sys.argv[i+1])
    here = os.path.dirname(os.path.abspath(__file__))
    # Canonical layout is <repo>/scripts/registry_audit.py (root = parent of scripts/).
    # Self-contained copies vendored at repo root instead need no extra hop up.
    return os.path.dirname(here) if os.path.basename(here) == "scripts" else here
ROOT = _root()
PRUNE_INHERITED = "--prune-inherited" in sys.argv  # fast mode: don't descend covered subtrees
MARKER = ".registry.yml"
STALE_DAYS = 30
COVERAGE_TARGET = 0.90  # framework seeds the top; improvement-session raises this over time

SKIP_DIRS = {".git", "__pycache__", "node_modules", "attic", ".rollback",
             ".pytest_cache", ".mypy_cache", "dist", "build", ".venv", "venv",
             ".ipynb_checkpoints", "unsloth_compiled_cache"}
SKIP_SUFFIX = ("-env", "-venv", ".egg-info", ".git")
TAXONOMY = {"service","registry","doc-of-record","code","script","test","config",
            "secret-ref","data","memory","experiment","retired","vendored","meta","archive"}
REQUIRED = {"category","purpose","owner","reconcile","last_audited","by"}

def parse_marker(path):
    """Minimal YAML: flat key: value. No deps."""
    d = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.rstrip("\n")
        if not line.strip() or line.strip().startswith("#"): continue
        m = re.match(r"^([a-zA-Z_]+):\s*(.*)$", line)
        if m: d[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return d

def significant(dirpath, filenames):
    """A dir is significant if it holds code/docs/config/data worth reasoning about."""
    for f in filenames:
        if f == MARKER: continue
        if f.endswith((".py",".ts",".js",".md",".yml",".yaml",".toml",".json",
                       ".sh",".db",".env",".service",".conf",".txt")):
            return True
    return False

def is_stale(ts):
    try:
        t = datetime.datetime.fromisoformat(ts.replace("Z","+00:00"))
        return (datetime.datetime.now(datetime.timezone.utc) - t).days > STALE_DAYS
    except Exception:
        return True  # unparseable stamp = stale

def walk():
    """Two passes: collect markers, then cover each dir via its nearest inheriting ancestor."""
    markers = {}          # rel -> dict(category, inherits, last_audited, malformed_reason)
    sig_dirs = []         # (rel, has_own_marker)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        parts = [p for p in rel.split(os.sep) if p != "."]
        if any(p in SKIP_DIRS or p.endswith(SKIP_SUFFIX) for p in parts):
            dirnames[:] = []; continue
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(SKIP_SUFFIX)]
        has_marker = MARKER in filenames
        if has_marker:
            mk = parse_marker(os.path.join(dirpath, MARKER))
            miss = REQUIRED - set(mk)
            bad = mk.get("category") not in TAXONOMY
            markers[rel] = {"category": mk.get("category"),
                            "inherits": mk.get("inherits","true").lower()=="true",
                            "last_audited": mk.get("last_audited",""),
                            "malformed": (f"missing:{sorted(miss)}" if miss else
                                          (f"bad category:{mk.get('category')}" if bad else None))}
        if has_marker or significant(dirpath, filenames):
            sig_dirs.append((rel, has_marker))
        if has_marker and PRUNE_INHERITED:
            m = markers.get(rel, {})
            if m.get("inherits") and not m.get("malformed"):
                dirnames[:] = []  # covered subtree — skip the deep/slow walk

    def nearest_inheriting_ancestor(rel):
        parts = rel.split(os.sep)
        for i in range(len(parts)-1, 0, -1):
            anc = os.sep.join(parts[:i])
            if anc in markers and markers[anc]["inherits"] and not markers[anc]["malformed"]:
                return markers[anc]["category"]
        # root-level marker (".")
        if "." in markers and markers["."]["inherits"] and not markers["."]["malformed"]:
            return markers["."]["category"]
        return None

    covered, uncovered, stale, malformed = [], [], [], []
    cats = {}
    for rel, has_own in sig_dirs:
        if has_own:
            m = markers[rel]
            if m["malformed"]:
                malformed.append((rel, m["malformed"])); covered.append(rel); continue
            cats[m["category"]] = cats.get(m["category"],0)+1
            if is_stale(m["last_audited"]): stale.append((rel, m["last_audited"]))
            covered.append(rel)
        else:
            anc = nearest_inheriting_ancestor(rel)
            if anc: covered.append(f"{rel}  (inherits {anc})")
            else: uncovered.append(rel)
    return covered, uncovered, stale, malformed, cats

def main():
    covered, uncovered, stale, malformed, cats = walk()
    total_sig = len(covered) + len(uncovered)
    cov_pct = (len(covered) / total_sig) if total_sig else 1.0
    report = {
        "coverage_pct": round(cov_pct*100, 1),
        "significant_dirs": total_sig, "covered": len(covered),
        "uncovered": len(uncovered), "stale": len(stale), "malformed": len(malformed),
        "by_category": dict(sorted(cats.items(), key=lambda kv:-kv[1])),
    }
    if "--json" in sys.argv:
        print(json.dumps({**report, "uncovered_list": uncovered,
                          "stale_list": stale, "malformed_list": malformed}, indent=1)); 
    else:
        print(f"CATALOG AUDIT — coverage {report['coverage_pct']}% "
              f"({report['covered']}/{report['significant_dirs']} significant dirs)")
        print(f"  categories: {report['by_category']}")
        if uncovered:
            print(f"  UNCOVERED ({len(uncovered)}) — add a .registry.yml or an inheriting ancestor:")
            for u in uncovered[:25]: print(f"    - {u}")
            if len(uncovered) > 25: print(f"    …and {len(uncovered)-25} more")
        if stale:
            print(f"  STALE (>{STALE_DAYS}d): " + ", ".join(f"{r}" for r,_ in stale[:15]))
        if malformed:
            print("  MALFORMED:")
            for r,why in malformed: print(f"    - {r}: {why}")
    healthy = cov_pct >= COVERAGE_TARGET and not stale and not malformed
    sys.exit(0 if healthy else 1)

if __name__ == "__main__":
    main()
