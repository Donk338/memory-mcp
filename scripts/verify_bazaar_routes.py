#!/usr/bin/env python3
"""verify_bazaar_routes.py — prove every x402 paid route publishes a VALID bazaar
discovery extension.

WHY (2026-09-29): memory_server.py logged, on every boot,
    UserWarning: x402: Route "GET /paid/memory/write" has an invalid bazaar
    extension: input/queryParams: 'content' is a required property
for all five paid routes. Cause: `declare_discovery_extension(input=...)` takes
EXAMPLE INPUT VALUES, not a transport descriptor. We were passing
`input={"type": "http", "method": "GET"}`, so the example queryParams were
literally {"type":"http","method":"GET"} — and validating that against our own
input_schema (required: ["content"]) fails. The method is inferred from the
route key by BazaarResourceServerExtension, never passed here.

The bazaar extension IS the discovery channel: it is what a Bazaar/402scan
crawler reads to learn how to build a correct call. An invalid one means our one
live revenue surface advertises a call shape no agent can satisfy.

WHAT THIS CHECKS (mirrors runtime, i.e. post-enrichment)
  1. importing memory_server raises no bazaar UserWarning (warnings -> errors);
  2. every route in http_routes carries extensions.bazaar;
  3. the published example queryParams satisfy the published queryParamsSchema
     once the method (inferred from the route key) is injected — the same
     validation the resource server runs at boot;
  4. GET routes with required params publish an example that actually contains
     them, so the discovery entry is instructive and callable.

Exit 0 = all green. Non-zero = the discovery surface is broken; do not ship.
"""
from __future__ import annotations

import copy
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FAILS: list[str] = []

# ---- 1. import clean, with bazaar warnings promoted to errors ---------------
with warnings.catch_warnings():
    warnings.filterwarnings("error", message=r".*bazaar extension.*")
    try:
        import memory_server  # noqa: E402
    except UserWarning as exc:  # noqa: BLE001
        print(f"FAIL import: {exc}")
        sys.exit(1)

from x402.extensions.bazaar import (  # noqa: E402
    BAZAAR,
    validate_discovery_extension,
)

routes = memory_server.http_routes
print(f"routes: {len(routes)}")

# ---- 2/3/4. every route declares a valid, instructive extension -------------
for key, cfg in sorted(routes.items()):
    method, _, path = key.partition(" ")
    ext = (cfg.get("extensions") or {}).get(BAZAAR.key)
    if not ext:
        FAILS.append(f"{key}: no {BAZAAR.key} extension")
        continue

    # the schema lives at schema.properties.input.properties.queryParams
    ischema = (((ext.get("schema") or {}).get("properties") or {})
               .get("input", {}).get("properties", {}).get("queryParams", {}))
    required = ischema.get("required") or []

    # runtime enrichment injects the method from the route key; mirror it
    enriched = copy.deepcopy(ext)
    enriched["info"]["input"]["method"] = method
    try:
        res = validate_discovery_extension(enriched)
        ok = getattr(res, "valid", True)
        errs = getattr(res, "errors", None)
    except Exception as exc:  # noqa: BLE001
        ok, errs = False, exc
    if not ok:
        FAILS.append(f"{key}: validate_discovery_extension -> {errs}")

    qp = (ext.get("info", {}).get("input") or {}).get("queryParams") or {}
    missing = [r for r in required if r not in qp]
    if missing:
        FAILS.append(f"{key}: example is missing required param(s) {missing}")
    if method == "GET" and required and not qp:
        FAILS.append(f"{key}: no example queryParams published")

    print(f"  {key:26s} example={qp} required={required} valid={ok}")

print()
if FAILS:
    print("FAILURES:")
    for f in FAILS:
        print(f"  - {f}")
    sys.exit(1)
print(f"OK — {len(routes)}/{len(routes)} paid routes publish valid bazaar discovery")
