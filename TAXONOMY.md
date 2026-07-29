<!-- REGISTRY
domain: taxonomy
last_reconciled: 2026-07-27T08:23:33Z
reconciled_by: estate-loop-sonnet
drift: none
reconcile: python3 registry_audit.py  # vendored at repo root here, not scripts/ (registry_audit.py's own _root() self-detects this)
-->

# The Catalog — one etiquette for every tree, branch, folder, file

Goal: every part of the estate is **categorised, stamped, auditable, and self-maintaining** — with
no per-file churn. The unit of etiquette is the **directory**, and files inherit their directory's
category. A single auditor (`scripts/registry_audit.py`) walks the whole tree, proves coverage, and
flags anything uncategorised, stale, or malformed. The improvement-session closes gaps autonomously.

## The marker: `.registry.yml` (one per significant directory)

A tiny sidecar file — non-invasive (never edits code), machine-parseable, inherited by subdirs:

```yaml
category: <one value from the taxonomy below>
purpose: <one line — what lives here and why>
owner: system | dave | ouroboros | vendor
reconcile: <shell command that verifies this area, or "n/a">
inherits: true            # subdirs without their own marker are covered by this one
last_audited: 2026-07-21T06:55Z
by: cowork-attended       # session tag
notes: <optional>
```

**Rules of etiquette (the same everywhere):**
1. Every significant directory is either (a) marked, or (b) covered by an ancestor marker with `inherits: true`. "Significant" = contains code, docs, config, or data a human/agent would reason about. (Skipped: `.git`, `__pycache__`, `node_modules`, venvs, `attic/`, build output.)
2. A directory with a **different** category than its parent MUST have its own marker (override).
3. Stamps follow the same clock-in/clock-out rule as registries: touch an area → re-stamp its marker (`last_audited`, `by`). Marker older than 30 days = stale → re-audit.
4. `reconcile` is the proof: a command that confirms the area is healthy/current. If it can't be a command, say how a human verifies it.
5. Secrets are never categorised INTO git — a directory that holds key material is `category: secret-ref` and its marker says where keys live, never their values.
6. `retired` marks deliberately-dead code/dirs (amputated, disabled-by-decision) — do NOT revive without Dave; the marker records why.

## Taxonomy (the controlled vocabulary — pick exactly one)

| category | what it means | example |
|---|---|---|
| `service` | a running/live paid or system service | boris x402 services |
| `registry` | a canonical ground-truth registry (this system) | memory/registry/ |
| `doc-of-record` | authoritative docs / skills / constitution | BIBLE.md, skills/ |
| `code` | application / library source | ouroboros/ modules |
| `script` | operational scripts & tooling | scripts/ |
| `test` | test suites | tests/ |
| `config` | env / flags / systemd / toml | .env, deploy/ |
| `secret-ref` | holds or points to key material | dirs with keys |
| `data` | databases / ledgers / runtime state | brain.db, *.db |
| `memory` | brain/memory artifacts (non-registry) | memory/knowledge/ |
| `experiment` | active experiment under the flag lifecycle | experiment branches/dirs |
| `retired` | deliberately dead — do not revive | amputated modules |
| `vendored` | third-party / generated / not ours to edit | vendored libs |
| `archive` | superseded material kept for reference — not live, do not revive | old project versions, backups (D:\Stuff) |
| `meta` | the catalog/registry machinery itself | this dir |

## Auditability

`python3 scripts/registry_audit.py --report` prints: total significant dirs, % covered, the list of
uncovered dirs, stale markers (>30d), malformed markers, and a category histogram. Exit 0 = healthy;
non-zero = gaps to close. This is the single command that makes the whole estate auditable at a glance.

## Portability — one etiquette, every device & location

The auditor is pure-stdlib Python and location-agnostic:
- `python3 scripts/registry_audit.py --root <path>` audits any tree, not just this repo.
- `--prune-inherited` = fast mode: stop descending a subtree once an inheriting marker covers it
  (use on large/slow locations like a Windows data drive).
- Each catalogued location is **self-contained**: it carries its own copy of the auditor + this
  TAXONOMY.md + its `.registry.yml` markers, so it stays auditable wherever it lives.

**Catalogued locations (the estate map):**
| Location | Host | Category | Status |
|---|---|---|---|
| `~/ouroboros-repo` | donk (WSL) | live code + memory | 100% (20 markers) |
| `D:\Stuff` (`/mnt/d/Stuff`) | Windows host | archive/data junk-drawer | 100% (9 markers, self-contained) |
| boris service repos (agent-rails, aegis, x402algo, memory-mcp) | boris (WSL) | service/code | 100% (16 markers: agent-rails 9, aegis 4, x402algo 2, memory-mcp 1 — self-contained, verified 2026-07-22) |

## Value mining — "always be mining"

Categorising is passive; the value is in prospecting. `scripts/registry_mine.py --root <path>`
scans any catalogued location for **ORE** (sellable products, salvageable code, valuable
models/data, backups) and **HAZARDS** (keys/creds on disk — flagged by shape, never opened).
Exit 2 = hazards present (a human must act). Think early-days mining: the estate already holds
built value; surface it and act before it's lost. Runs continuously via the improvement-session.

## Autonomy

- The daily/2-hourly improvement-session runs the auditor as a ground-truth step; each uncovered or
  stale directory is a finding it can close (add/refresh a marker) under change-control.
- Coverage only goes up: new directories surface as findings the next pass. No big-bang migration.
- Drift (a `reconcile` that now fails, a stale stamp) is reported, never silently tolerated.
