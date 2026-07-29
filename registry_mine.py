#!/usr/bin/env python3
"""Value miner — the catalog's prospecting pass. "Always be mining."

Scans a catalogued location for ORE (value) and HAZARDS (risk) by filename/shape
(never reads secret contents). Complements registry_audit.py: the auditor proves
everything is categorised; the miner surfaces what's worth acting on.

  python3 scripts/registry_mine.py --root <path> [--report]

Ore classes:  product (sellable) · code (salvageable) · model/data (valuable) · archive
Hazard class: secret (keys/creds on disk — flag, never open)
"""
import os, re, sys, json

def root():
    for i,a in enumerate(sys.argv):
        if a=="--root" and i+1<len(sys.argv): return os.path.abspath(sys.argv[i+1])
    here = os.path.dirname(os.path.abspath(__file__))
    # Canonical layout is <repo>/scripts/registry_mine.py (root = parent of scripts/).
    # Self-contained copies vendored at repo root instead need no extra hop up.
    return os.path.dirname(here) if os.path.basename(here) == "scripts" else here
ROOT = root()
SKIP = {".git","__pycache__","node_modules",".cache",".pytest_cache",".mypy_cache",
        "dist","build",".venv","venv"}
MAXDEPTH = 4

# (label, regex on lowercased name, action) — order = priority
SIGNALS = [
    ("HAZARD:secret", r"(^id_rsa$|^id_ed25519$|_rsa$|\.pem$|\.p12$|\.pfx$|(^|\.)env($|\.)|credentials$|(^|_)(creds?|tokens?)\.(txt|json)$|[._]key$|api[_-]?key|secret|^\.(npmrc|pypirc|netrc)$)", "SECURE/rotate — do not commit, move to encrypted store"),
    ("ORE:product",   r"(gumroad|listing|toolkit.*\.zip|product.*\.zip|-v\d+\.zip)", "SHIP or list — potential sellable digital product"),
    ("ORE:model",     r"(\.gguf$|\.safetensors$|\.bin$|\.onnx$|\.pt$|\.ckpt$)", "valuable weights — keep, catalog, don't lose"),
    ("ORE:data",      r"(\.db$|\.sqlite$|\.parquet$|\.csv$|dataset)", "data asset — assess for reuse/value"),
    ("ORE:archive",   r"(backup|-\d{8}t\d|\.zip$|\.tar\.gz$|\.tgz$|\.7z$)", "backup — dedupe/retain-or-delete"),
]

def scan():
    hits = {}   # label -> list[(path, action)]
    for dp, dns, fns in os.walk(ROOT):
        rel = os.path.relpath(dp, ROOT)
        depth = 0 if rel=="." else rel.count(os.sep)+1
        parts = [p for p in rel.split(os.sep) if p!="."]
        if any(p in SKIP for p in parts) or depth>MAXDEPTH:
            dns[:] = []; continue
        dns[:] = [d for d in dns if d not in SKIP]
        # a sensitive dir name is itself a hazard signal
        for d in dns:
            if d in (".ssh",".aws",".azure",".gnupg"):
                hits.setdefault("HAZARD:secret", []).append((os.path.join(rel,d)+"/", "SECURE — credential directory on disk"))
        for f in fns:
            low = f.lower()
            for label, pat, action in SIGNALS:
                if label=="HAZARD:secret" and re.search(r"\.(example|sample|template|md)$|^added_tokens\.json$|:zone\.identifier$", low):
                    continue  # templates/docs/tokenizer artifacts/Windows ADS metadata stubs are not live secrets
                if label=="ORE:archive" and re.search(r"\.(py|sh|js|ts|md|json)$", low):
                    continue  # source/scripts/docs and dated message-queue JSON aren't backup archives, even if named *backup* or timestamped
                if re.search(pat, low):
                    hits.setdefault(label, []).append((os.path.join(rel,f), action)); break
    return hits

def main():
    hits = scan()
    order = ["HAZARD:secret","ORE:product","ORE:model","ORE:data","ORE:archive"]
    if "--json" in sys.argv:
        print(json.dumps({k:hits.get(k,[]) for k in order}, indent=1)); return
    print(f"MINE — {ROOT}")
    for k in order:
        v = hits.get(k, [])
        if not v: continue
        print(f"\n{k}  ({len(v)})  → {v[0][1]}")
        for path,_ in v[:12]: print(f"    {path}")
        if len(v)>12: print(f"    …and {len(v)-12} more")
    haz = len(hits.get("HAZARD:secret",[]))
    print(f"\nsummary: {sum(len(v) for v in hits.values())} signals, {haz} HAZARD.")
    sys.exit(2 if haz else 0)  # exit 2 = hazards present (needs a human)

if __name__ == "__main__":
    main()
