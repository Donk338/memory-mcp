"""Free-tier caps must be env-overridable (the pay-trigger dial) with defaults unchanged.
Each case imports memory_server fresh in a subprocess so module-level env reads are honest."""
import os, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def caps(env_extra):
    env = dict(os.environ)
    env.update(env_extra)
    code = (
        "import sys; sys.path.insert(0, %r); import memory_server as M; "
        "print(M.FREE_WRITE_CAP, M.FREE_SEARCH_CAP, M.FREE_WINDOW_DAYS)" % str(HERE)
    )
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, (out.returncode, out.stderr[-2000:])
    return out.stdout.split()


# 1. defaults unchanged — the dial does nothing until a human turns it
assert caps({}) == ["1000", "500", "30"], caps({})
print("PASS defaults unchanged: 1000 / 500 / 30")

# 2. env override wins (a genuine-trial setting)
assert caps({"MEM_FREE_WRITE_CAP": "20", "MEM_FREE_SEARCH_CAP": "10", "MEM_FREE_WINDOW_DAYS": "7"}) == ["20", "10", "7"]
print("PASS env override: 20 / 10 / 7")

# 3. garbage never crashes the server — falls back to default
assert caps({"MEM_FREE_WRITE_CAP": "not-a-number"}) == ["1000", "500", "30"]
print("PASS garbage falls back to default")

# 4. zero is honoured (means "no free tier")
assert caps({"MEM_FREE_WRITE_CAP": "0", "MEM_FREE_SEARCH_CAP": "0"}) == ["0", "0", "30"]
print("PASS zero honoured (free tier off)")

# 5. cap enforcement still fires and points at the paid tier
sys.path.insert(0, str(HERE))
import memory_server as M  # noqa: E402

M._free_tier_used = lambda ns, tools: 10 ** 9  # simulate a wallet over cap
try:
    M._check_free_cap("0x" + "a" * 40, M.FREE_SEARCH_TOOLS, M.FREE_SEARCH_CAP)
    raise AssertionError("over-cap wallet was NOT refused")
except ValueError as e:
    assert "free-tier cap reached" in str(e) and "subscribe" in str(e), str(e)
print("PASS over-cap wallet refused with upgrade pointer")
print("ALL PASS")
