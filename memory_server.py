"""memory-mcp — persistent agent memory sold per-call via x402. Port 8407.
Namespace = payer wallet address (your wallet is your memory)."""
import asyncio, base64, contextlib, json, re, sqlite3, time
from pathlib import Path

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from x402 import x402ResourceServerSync, ResourceConfig
from x402.http import HTTPFacilitatorClientSync, FacilitatorConfig
from x402.mechanisms.evm.exact import ExactEvmServerScheme
from x402.mcp import create_payment_wrapper_sync
from x402.mcp.types import SyncPaymentWrapperConfig, ResourceInfo, MCPToolResult

BASE = Path(__file__).parent
cfg = dict(l.strip().split("=", 1) for l in open(BASE / "config.env") if "=" in l)
PAY_TO, NETWORK, EMBED_URL = cfg["PAY_TO_ADDRESS"], cfg["NETWORK"], cfg["EMBED_URL"]
MEMDB, CALLDB = BASE / "memories.db", BASE / "memory-calls.db"
MAX_CONTENT, MAX_EXPORT = 8000, 1000
VALID_NS = re.compile(r"^0x[0-9a-f]{40}$")  # payer wallets — fail closed otherwise
VALID_NS_EXT = re.compile(r"^(0x[0-9a-f]{40}|agent:8453:\d+|trial:[0-9a-f]{12})$")  # server-constructed namespaces

def _mem():
    c = sqlite3.connect(MEMDB)
    c.execute("""CREATE TABLE IF NOT EXISTS memories(
        id INTEGER PRIMARY KEY, ns TEXT NOT NULL, ts REAL, content TEXT, tags TEXT, vec TEXT, dim INT)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ns ON memories(ns)")
    return c
def _calls():
    c = sqlite3.connect(CALLDB)
    c.execute("""CREATE TABLE IF NOT EXISTS calls(
        id INTEGER PRIMARY KEY, ts REAL, tool TEXT, ns TEXT, paid INT, price TEXT, latency_ms REAL, err TEXT)""")
    return c
_mem().close(); _calls().close()

def log_call(tool, ns, paid, price, t0, err=""):
    with contextlib.suppress(Exception):
        c = _calls()
        c.execute("INSERT INTO calls(ts,tool,ns,paid,price,latency_ms,err) VALUES(?,?,?,?,?,?,?)",
                  (t0, tool, ns, paid, price, (time.time() - t0) * 1000, err[:200]))
        c.commit(); c.close()

def embed(texts):
    r = httpx.post(EMBED_URL, json={"texts": texts, "normalize": True}, timeout=30)
    r.raise_for_status()
    j = r.json()
    return j["vectors"] if isinstance(j, dict) else j

def payer_from_extra(extra):
    """Extract payer wallet from x402 payment meta. Namespace = payer address."""
    try:
        meta = getattr(extra, "meta", None)
        if meta is None: meta = (extra or {}).get("_meta", {}) if isinstance(extra, dict) else {}
        p = meta.get("x402/payment")
        if p is None: return None
        if isinstance(p, str):
            with contextlib.suppress(Exception):
                p = json.loads(base64.b64decode(p))
            if isinstance(p, str): p = json.loads(p)
        payload = p.get("payload", p)
        auth = payload.get("authorization", payload)
        addr = auth.get("from") or auth.get("from_") or auth.get("sender")
        if not addr: return None
        addr = addr.lower()
        return addr if VALID_NS.match(addr) else None
    except Exception:
        return None

# ---- tool impls (a = args, ns = payer namespace) ----
def _require_ns(ns):
    if not ns or not VALID_NS_EXT.match(ns):
        raise ValueError("payer wallet could not be determined from the x402 payment; "
                         "call rejected (fail-closed) so your data never lands in a shared namespace")

# ---- namespace extensions: agent-keyed (ERC-8004) + shared (signed grants) ----
GRANT_MAX_DAYS = 90

def _grant_typed(namespace, grantee, mode, expires):
    return {"types": {"EIP712Domain": [{"name": "name", "type": "string"}, {"name": "version", "type": "string"},
                                       {"name": "chainId", "type": "uint256"}],
                      "Grant": [{"name": "namespace", "type": "address"}, {"name": "grantee", "type": "address"},
                                {"name": "mode", "type": "string"}, {"name": "expires", "type": "uint256"}]},
            "primaryType": "Grant",
            "domain": {"name": "BorisMemory", "version": "1", "chainId": 8453},
            "message": {"namespace": namespace, "grantee": grantee, "mode": mode, "expires": int(expires)}}

def _verify_grant(blob, payer, need_write):
    if not blob:
        raise ValueError("on_behalf requires grant= (base64 JSON {namespace,grantee,mode,expires,signature})")
    g = json.loads(base64.b64decode(blob))
    owner, grantee = g["namespace"].lower(), g["grantee"].lower()
    mode, exp = g.get("mode", "read"), int(g["expires"])
    if not VALID_NS.match(owner): raise ValueError("grant namespace must be a wallet address")
    if grantee != payer: raise ValueError("grant grantee != paying wallet")
    if time.time() > exp: raise ValueError("grant expired")
    if exp > time.time() + GRANT_MAX_DAYS * 86400: raise ValueError(f"grant expiry too far (max {GRANT_MAX_DAYS}d)")
    if need_write and mode not in ("write", "rw"): raise ValueError(f"grant mode '{mode}' does not permit write")
    from eth_account import Account
    from eth_account.messages import encode_typed_data
    td = _grant_typed(owner, grantee, mode, exp)
    rec = Account.recover_message(encode_typed_data(full_message=td), signature=g["signature"]).lower()
    if rec != owner: raise ValueError("grant signature invalid (EOA signatures only in v1)")
    return owner

def _agent_ns(agent_id, payer):
    """ERC-8004-keyed namespace: memory follows the on-chain agent, not the wallet.
    Payer must be the agent's attested agentWallet (setAgentWallet)."""
    if not payer or not VALID_NS.match(payer):
        raise ValueError("agent-keyed memory requires a wallet payer")
    c = _mem()
    c.execute("CREATE TABLE IF NOT EXISTS agent_wallet_cache(agent_id INTEGER PRIMARY KEY, wallet TEXT, ts REAL)")
    row = c.execute("SELECT wallet, ts FROM agent_wallet_cache WHERE agent_id=?", (int(agent_id),)).fetchone()
    if not row or time.time() - row[1] > 3600:
        import sys as _s
        if "/home/donk/aegis" not in _s.path: _s.path.insert(0, "/home/donk/aegis")
        import erc8004 as _e
        w = _e.get_agent_wallet(int(agent_id)).lower()
        c.execute("""INSERT INTO agent_wallet_cache(agent_id,wallet,ts) VALUES(?,?,?)
                     ON CONFLICT(agent_id) DO UPDATE SET wallet=excluded.wallet, ts=excluded.ts""",
                  (int(agent_id), w, time.time()))
        c.commit()
    else:
        w = row[0]
    c.close()
    if w != payer:
        raise ValueError(f"agent #{agent_id} agentWallet is {w or 'unset'}, not your paying wallet — "
                         "attest your wallet on-chain via ERC-8004 setAgentWallet to use agent-keyed memory")
    return f"agent:8453:{int(agent_id)}"

def _remap_ns(a, ns, write=False):
    if a.get("agent_id") is not None:
        return _agent_ns(int(a["agent_id"]), ns)
    if a.get("on_behalf"):
        owner = _verify_grant(a.get("grant", ""), ns, write)
        if owner != str(a["on_behalf"]).lower(): raise ValueError("grant namespace != on_behalf")
        return owner
    return ns

def t_write(a, ns):
    ns = _remap_ns(a, ns, write=True)
    _require_ns(ns)
    content = str(a["content"])[:MAX_CONTENT]
    tags = ",".join(str(t) for t in a.get("tags", []))[:400]
    vec, dim = None, 0
    with contextlib.suppress(Exception):
        v = embed([content])[0]; vec, dim = json.dumps(v), len(v)
    c = _mem()
    cur = c.execute("INSERT INTO memories(ns,ts,content,tags,vec,dim) VALUES(?,?,?,?,?,?)",
                    (ns, time.time(), content, tags, vec, dim))
    c.commit(); mid = cur.lastrowid; c.close()
    return {"id": mid, "namespace": ns, "embedded": dim > 0}

def t_search(a, ns):
    ns = _remap_ns(a, ns, write=False)
    _require_ns(ns)
    q = str(a["query"])[:1000]
    k = min(int(a.get("k", 5)), 20)
    c = _mem()
    rows = c.execute("SELECT id,ts,content,tags,vec FROM memories WHERE ns=?", (ns,)).fetchall()
    c.close()
    if not rows: return {"namespace": ns, "results": [], "note": "no memories stored for this wallet"}
    results = []
    qv = None
    with contextlib.suppress(Exception): qv = embed([q])[0]
    if qv:
        scored = []
        for (mid, ts, content, tags, vec) in rows:
            if not vec: continue
            v = json.loads(vec)
            scored.append((sum(x * y for x, y in zip(qv, v)), mid, ts, content, tags))
        scored.sort(reverse=True)
        results = [{"id": m, "score": round(s, 4), "ts": ts, "content": ct, "tags": tg}
                   for s, m, ts, ct, tg in scored[:k]]
    if not results:  # keyword fallback
        ql = q.lower()
        hits = [(mid, ts, ct, tg) for (mid, ts, ct, tg, _) in rows if ql in (ct or "").lower()]
        results = [{"id": m, "score": None, "ts": ts, "content": ct, "tags": tg}
                   for m, ts, ct, tg in hits[:k]]
    return {"namespace": ns, "query": q, "results": results}

def t_export(a, ns):
    ns = _remap_ns(a, ns, write=False)
    _require_ns(ns)
    c = _mem()
    rows = c.execute("SELECT id,ts,content,tags FROM memories WHERE ns=? ORDER BY ts DESC LIMIT ?",
                     (ns, MAX_EXPORT)).fetchall()
    c.close()
    return {"namespace": ns, "count": len(rows),
            "memories": [{"id": m, "ts": ts, "content": ct, "tags": tg} for m, ts, ct, tg in rows]}

def t_stats(a, _ns_unused=None):
    ns = str(a.get("namespace", "")).lower()
    if not VALID_NS_EXT.match(ns): return {"error": "namespace must be a wallet address, agent:8453:N, or trial id"}
    c = _mem()
    n, last = c.execute("SELECT COUNT(*), MAX(ts) FROM memories WHERE ns=?", (ns,)).fetchone()
    c.close()
    return {"namespace": ns, "count": n, "last_write_ts": last}  # counts only — content never free

# ---- x402 plumbing ----
facilitator = HTTPFacilitatorClientSync(FacilitatorConfig(url=cfg["FACILITATOR_URL"]))
rs = x402ResourceServerSync(facilitator)
rs.register(NETWORK, ExactEvmServerScheme())
rs.initialize()

# Bazaar indexes ONLY https resources observed settling via the facilitator — mcp://
# URIs are invisible to it. Map each paid tool to its real https path so the 402
# challenge (and any discovery surface built from it) is bazaar-indexable.
TOOL_HTTP_PATH = {
    "memory_write": "/paid/memory/write",
    "memory_search": "/paid/memory/search",
    "memory_export": "/paid/memory/export",
}

def paid(tool, price, fn):
    path = TOOL_HTTP_PATH.get(tool, "/paid/memory/" + tool.replace("memory_", "", 1))
    wrapper = create_payment_wrapper_sync(rs, SyncPaymentWrapperConfig(
        accepts=rs.build_payment_requirements(ResourceConfig(scheme="exact", network=NETWORK, pay_to=PAY_TO, price=price)),
        resource=ResourceInfo(url=f"https://mem.borisinc.com{path}", description=f"memory-mcp {tool}")))
    def handler(args, extra):
        def inner(a, e):
            t0 = time.time()
            ns = payer_from_extra(e)
            if not ns:
                log_call(tool, "REJECTED:no-payer", 1, price, t0, "payer extraction failed - fail-closed")
                raise ValueError("payer wallet could not be determined from x402 payment; call rejected")
            try:
                res = fn(a, ns); log_call(tool, ns, 1, price, t0)
                return MCPToolResult(content=[{"type": "text", "text": json.dumps(res)}])
            except Exception as ex:
                log_call(tool, ns, 1, price, t0, str(ex)); raise
        return wrapper(inner)(args, extra)
    return handler

def free(tool, fn):
    def handler(args, extra):
        t0 = time.time()
        try:
            res = fn(args, None); log_call(tool, "", 0, "$0", t0)
            return MCPToolResult(content=[{"type": "text", "text": json.dumps(res)}])
        except Exception as ex:
            log_call(tool, "", 0, "$0", t0, str(ex)); raise
    return handler

PRICES = {"memory_write": "$0.001", "memory_search": "$0.002", "memory_export": "$0.01"}
HANDLERS = {
    "memory_write": paid("memory_write", "$0.001", t_write),
    "memory_search": paid("memory_search", "$0.002", t_search),
    "memory_export": paid("memory_export", "$0.01", t_export),
    "memory_stats": free("memory_stats", t_stats),
}

TOOLS = [
    types.Tool(name="memory_write",
        description="Store a memory permanently. Namespaced to YOUR paying wallet — your wallet is your private memory store. Semantic-indexed for later search. Paid: $0.001 per write via x402 (payment in _meta['x402/payment']).",
        inputSchema={"type": "object", "properties": {
            "content": {"type": "string", "description": "The memory text (max 8000 chars)"},
            "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["content"]}),
    types.Tool(name="memory_search",
        description="Semantic search over YOUR stored memories (payer-wallet namespace). Returns top-k by embedding similarity. Paid: $0.002 per search via x402.",
        inputSchema={"type": "object", "properties": {
            "query": {"type": "string"}, "k": {"type": "integer", "description": "max results (default 5, cap 20)"}},
            "required": ["query"]}),
    types.Tool(name="memory_export",
        description="Export ALL memories in YOUR payer-wallet namespace (up to 1000, newest first). Your data is yours. Paid: $0.01 via x402.",
        inputSchema={"type": "object", "properties": {}}),
    types.Tool(name="memory_stats",
        description="FREE: count + last-write timestamp for any wallet namespace. Content is never returned on the free tier.",
        inputSchema={"type": "object", "properties": {
            "namespace": {"type": "string", "description": "wallet address"}}, "required": ["namespace"]}),
]

server = Server("memory-mcp")

# --- Boris Inc web layer v2 -------------------------------------------------
from pathlib import Path as _WPath
_WEB_DIST = _WPath("/home/donk/borisinc-web/dist")
_web_cache: dict = {}
def web_asset(name: str) -> str:
    try:
        p = _WEB_DIST / name
        m = p.stat().st_mtime
    except OSError:
        return ""
    hit = _web_cache.get(name)
    if not hit or hit[0] != m:
        try:
            _web_cache[name] = (m, p.read_text(encoding="utf-8"))
        except OSError:
            return ""
    return _web_cache[name][1]
# ---------------------------------------------------------------------------


@server.list_tools()
async def list_tools(): return TOOLS

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name not in HANDLERS: raise ValueError(f"unknown tool {name}")
    meta = {}
    with contextlib.suppress(Exception):
        m = server.request_context.meta
        if m is not None: meta = m.model_dump(by_alias=True)
    res = await asyncio.to_thread(HANDLERS[name], arguments or {}, {"_meta": meta, "toolName": name})
    payload = {"content": res.content, "isError": res.is_error}
    if res.meta: payload["_meta"] = res.meta
    if res.structured_content: payload["structuredContent"] = res.structured_content
    return types.CallToolResult.model_validate(payload)

session_manager = StreamableHTTPSessionManager(app=server, json_response=True, stateless=True)

# ---------- HTTP x402 surface (FastAPI) ----------
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from x402 import x402ResourceServer
from x402.http import HTTPFacilitatorClient
from x402.http.middleware.fastapi import payment_middleware
from x402.extensions.bazaar import declare_discovery_extension, OutputConfig

def _backfill_embeddings():
    """Self-heal: embed any rows stored while the embedder (donk) was down."""
    c = _mem()
    rows = c.execute("SELECT id, content FROM memories WHERE vec IS NULL LIMIT 200").fetchall()
    done = 0
    for mid, content in rows:
        try:
            v = embed([content])[0]
            c.execute("UPDATE memories SET vec=?, dim=? WHERE id=?", (json.dumps(v), len(v), mid))
            c.commit(); done += 1
        except Exception:
            break  # embedder still down — retry next cycle
    c.close()
    return done, len(rows)

async def _backfill_loop():
    while True:
        with contextlib.suppress(Exception):
            done, pending = await asyncio.to_thread(_backfill_embeddings)
            if done: print(f"backfill: embedded {done}/{pending} orphan memories", flush=True)
        await asyncio.sleep(3600)

@asynccontextmanager
async def lifespan(app):
    task = asyncio.create_task(_backfill_loop())
    try:
        async with session_manager.run():
            yield
    finally:
        task.cancel()

fapp = FastAPI(title="memory-mcp", version="1.0.0",
    description="Persistent memory for AI agents, paid per call via x402 (USDC, Base mainnet) — your wallet is your private, semantically-indexed memory namespace. Write $0.001, search $0.002, export $0.01; free trial + $2/30d unlimited subscription. MCP + HTTP. No account, no API key.",
    contact={"name": "Boris Inc", "email": "donk.boris@mailfence.com", "url": "https://borisinc.com"},
    docs_url=None, lifespan=lifespan)

afacilitator = HTTPFacilitatorClient(FacilitatorConfig(url=cfg["FACILITATOR_URL"]))
aserver = x402ResourceServer(afacilitator)
aserver.register(NETWORK, ExactEvmServerScheme())

def accepts(price): return {"accepts": {"scheme": "exact", "payTo": PAY_TO, "price": price, "network": NETWORK}}
def _x402_atomic(price):
    """'$0.02' -> '20000' (USDC, 6dp). Canonical x402 amounts are atomic ints."""
    return str(int(round(float(str(price).strip().lstrip("$")) * 1_000_000)))

def _x402_items(entries, network, pay_to):
    """entries: [(resource_url, price_str, description, method)] -> canonical items[]."""
    out = []
    for res, price, desc, method in entries:
        amt = _x402_atomic(price)
        out.append({"resource": res, "type": "http", "x402Version": 2, "method": method,
                    "accepts": [{"scheme": "exact", "network": network, "amount": amt,
                                 "maxAmountRequired": amt, "resource": res, "method": method,
                                 "description": desc, "mimeType": "application/json",
                                 "payTo": pay_to, "maxTimeoutSeconds": 300,
                                 "asset": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"}]})
    return out

HTTP_PRICES = {"/paid/memory/write": "$0.001", "/paid/memory/search": "$0.002", "/paid/memory/export": "$0.01"}
http_routes = {
    "GET /paid/memory/write": {**accepts("$0.001"), "extensions": declare_discovery_extension(
        input={"type": "http", "method": "GET"},
        input_schema={"properties": {"content": {"type": "string", "description": "Memory text to store (max 8000 chars)"},
                                     "tags": {"type": "string", "description": "Comma-separated tags"}}, "required": ["content"]},
        output=OutputConfig(example={"id": 42, "namespace": "0xabc...", "embedded": True}))},
    "GET /paid/memory/search": {**accepts("$0.002"), "extensions": declare_discovery_extension(
        input={"type": "http", "method": "GET"},
        input_schema={"properties": {"query": {"type": "string", "description": "Semantic search query"},
                                     "k": {"type": "integer", "description": "Max results (default 5, cap 20)"}}, "required": ["query"]},
        output=OutputConfig(example={"namespace": "0xabc...", "results": [{"id": 42, "score": 0.83, "content": "User prefers metric units"}]}))},
    "GET /paid/memory/export": {**accepts("$0.01"), "extensions": declare_discovery_extension(
        input={"type": "http", "method": "GET"},
        output=OutputConfig(example={"namespace": "0xabc...", "count": 2, "memories": [{"id": 42, "content": "..."}]}))},
}
x402_mw = payment_middleware(http_routes, aserver)

SUB_PRICE, SUB_DAYS = "$2.00", 30
def _subs():
    c = _calls()
    c.execute("CREATE TABLE IF NOT EXISTS mem_subs(token TEXT PRIMARY KEY, wallet TEXT, created REAL, expires REAL)")
    return c
_subs().close()

def _sub_wallet_for(request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer mem_"): return None
    tok = auth.split(" ", 1)[1].strip()
    try:
        c = _subs(); row = c.execute("SELECT wallet, expires FROM mem_subs WHERE token=?", (tok,)).fetchone(); c.close()
    except Exception:
        return None
    if not row or row[1] < time.time(): return None
    return row[0]

@fapp.middleware("http")
async def paywall(request: Request, call_next):
    if request.url.path in HTTP_PRICES:
        w = _sub_wallet_for(request)
        if w:
            request.state.sub_wallet = w
            return await call_next(request)
    return await x402_mw(request, call_next)

@fapp.middleware("http")
async def x402_body_mirror(request: Request, call_next):
    """x402scan & non-SDK agents parse the 402 body, not the payment-required
    header. Mirror the v2 challenge JSON into the body (spec allows both)."""
    resp = await call_next(request)
    hdr = resp.headers.get("payment-required")
    if resp.status_code == 402 and hdr:
        with contextlib.suppress(Exception):
            import base64
            from fastapi.responses import Response
            body = base64.b64decode(hdr + "=" * (-len(hdr) % 4))
            json.loads(body)  # must be valid JSON or keep original response
            headers = dict(resp.headers)
            headers.pop("content-length", None)
            headers.pop("content-type", None)
            return Response(content=body, status_code=402, headers=headers,
                            media_type="application/json")
    return resp

@fapp.middleware("http")
async def call_log(request, call_next):
    t0 = time.time(); resp = await call_next(request)
    with contextlib.suppress(Exception):
        p = request.url.path
        if p.startswith("/paid/"):
            log_call("http:" + p, getattr(request.state, "payer_ns", ""),
                     1 if resp.status_code == 200 else 0, HTTP_PRICES.get(p, "?"), t0,
                     "" if resp.status_code in (200, 402) else f"http {resp.status_code}")
    return resp

def payer_from_request(request) -> str:
    try:
        pp = request.state.payment_payload
        d = pp.model_dump(by_alias=True) if hasattr(pp, "model_dump") else dict(pp)
        def dig(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("from", "from_", "sender") and isinstance(v, str) and v.startswith("0x"): return v
                    r = dig(v)
                    if r: return r
            return None
        addr = dig(d)
        ns = addr.lower() if addr else None
        if ns and not VALID_NS.match(ns): ns = None
    except Exception:
        ns = None
    if not ns:
        ns = getattr(request.state, "sub_wallet", None)  # active subscription covers the call
    request.state.payer_ns = ns or "REJECTED:no-payer"
    return ns

def _http_args(agent_id, on_behalf, grant):
    a = {}
    if agent_id is not None: a["agent_id"] = agent_id
    if on_behalf: a["on_behalf"] = on_behalf
    if grant: a["grant"] = grant
    return a

@fapp.get("/paid/memory/write")
async def http_write(request: Request, content: str, tags: str = "", agent_id: int = None, on_behalf: str = "", grant: str = ""):
    ns = payer_from_request(request)
    if not ns: return JSONResponse({"error": "payer wallet could not be determined; rejected fail-closed"}, status_code=400)
    try:
        return await asyncio.to_thread(t_write, {"content": content, "tags": [t for t in tags.split(",") if t],
                                                 **_http_args(agent_id, on_behalf, grant)}, ns)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=403)

@fapp.get("/paid/memory/search")
async def http_search(request: Request, query: str, k: int = 5, agent_id: int = None, on_behalf: str = "", grant: str = ""):
    ns = payer_from_request(request)
    if not ns: return JSONResponse({"error": "payer wallet could not be determined; rejected fail-closed"}, status_code=400)
    try:
        return await asyncio.to_thread(t_search, {"query": query, "k": k, **_http_args(agent_id, on_behalf, grant)}, ns)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=403)

@fapp.get("/paid/memory/export")
async def http_export(request: Request, agent_id: int = None, on_behalf: str = "", grant: str = ""):
    ns = payer_from_request(request)
    if not ns: return JSONResponse({"error": "payer wallet could not be determined; rejected fail-closed"}, status_code=400)
    try:
        return await asyncio.to_thread(t_export, _http_args(agent_id, on_behalf, grant), ns)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=403)

@fapp.get("/memory/stats", openapi_extra={"security": []})
async def http_stats(namespace: str):
    return t_stats({"namespace": namespace})


# --- same-origin static assets (see borisinc-web/README.md) ----------------
@fapp.get("/brand.css", include_in_schema=False, openapi_extra={"security": []})
async def _brand_css():
    from fastapi.responses import Response
    return Response(content=web_asset("brand.css"), media_type="text/css",
                    headers={"Cache-Control": "public, max-age=300"})

@fapp.get("/favicon.svg", include_in_schema=False, openapi_extra={"security": []})
async def _favicon_svg():
    from fastapi.responses import Response
    return Response(content=web_asset("favicon.svg"), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})

@fapp.get("/boris.js", include_in_schema=False, openapi_extra={"security": []})
async def _boris_js():
    from fastapi.responses import Response
    return Response(content=web_asset("boris.js"), media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})
# ---------------------------------------------------------------------------

@fapp.get("/health", openapi_extra={"security": []})
async def health(): return {"ok": True, "service": "memory-mcp", "network": NETWORK}

@fapp.get("/", openapi_extra={"security": []})
async def index(request: Request):
    if "text/html" in request.headers.get("accept", ""):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=(web_asset("memory.html")
                                     or open(BASE / "landing.html").read()))
    return {"service": "memory-mcp", "tagline": "Persistent memory for AI agents — your wallet is your memory.",
            "http": {p: HTTP_PRICES[p] for p in HTTP_PRICES},
            "free": ["/trial/memory/write (5 records, 24h, not private)", "/trial/memory/search",
                     "/memory/stats?namespace=", "/grant/payload", "/health", "/llms.txt", "/quickstart"],
            "subscription": {"GET /subscribe": "$2.00 for 30d unlimited ops, wallet-bound bearer token"},
            "namespaces": {"wallet": "default: payer address", "agent": "agent_id= → ERC-8004 agent-keyed (requires on-chain setAgentWallet)",
                           "shared": "on_behalf= + grant= (owner-signed EIP-712 grant, read|write|rw, max 90d)"},
            "mcp": "https://mem.borisinc.com/mcp (memory_write $0.001, memory_search $0.002, memory_export $0.01, memory_stats free; agent_id/on_behalf/grant args supported)",
            "network": NETWORK, "protocol": "x402/v2", "docs": "/openapi.json"}


def _sitemap_xml(host, paths):
    urls = "".join(f"<url><loc>https://{host}{p}</loc><changefreq>daily</changefreq></url>" for p in paths)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'

@fapp.get("/sitemap.xml", include_in_schema=False)
async def sitemap():
    from fastapi.responses import Response
    return Response(content=_sitemap_xml("mem.borisinc.com", ["/", "/llms.txt", "/quickstart", "/subscribe", "/trial/memory/write", "/grant/payload"]), media_type="application/xml")

@fapp.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots():
    return "User-agent: *\nAllow: /\nSitemap: https://mem.borisinc.com/sitemap.xml"

@fapp.get("/quickstart", include_in_schema=False)
async def quickstart():
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=open(BASE / "quickstart.html").read())

@fapp.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(content=open(BASE / "favicon.ico", "rb").read(), media_type="image/x-icon")

@fapp.get("/llms.txt", response_class=PlainTextResponse, openapi_extra={"security": []})
async def llms():
    return """# memory-mcp — persistent memory for AI agents (mem.borisinc.com)
> Your wallet is your memory. Pay per call via x402 (Base mainnet USDC), no account, no API key. Memories are namespaced to the paying wallet and semantically indexed.
HTTP: GET /paid/memory/write?content=&tags= ($0.001) . GET /paid/memory/search?query=&k= ($0.002) . GET /paid/memory/export ($0.01) . free GET /memory/stats?namespace=
FREE TRIAL: /trial/memory/write + /trial/memory/search — 5 records, 24h TTL, IP-keyed, NOT private. Taste the loop, then bring a wallet.
SUBSCRIPTION: GET /subscribe — $2.00 for 30 days unlimited ops on your wallet namespace (bearer token).
AGENT-KEYED MEMORY (ERC-8004): add agent_id= to any call — memory binds to your on-chain agent (Base IdentityRegistry), survives wallet rotation. Requires setAgentWallet attestation; payer must be the agentWallet.
SHARED MEMORY (grants): namespace owner signs an EIP-712 grant (GET /grant/payload builds it); grantee calls with on_behalf= and grant=. read|write|rw, max 90 days. Multi-agent teams share one memory.
MCP: https://mem.borisinc.com/mcp — memory_write / memory_search / memory_export / memory_stats(free), x402 payment in _meta; agent_id/on_behalf/grant args supported.
Discovery: /openapi.json . Human quickstart: /quickstart . 402 challenge in payment-required header."""

async def app(scope, receive, send):
    if scope["type"] == "http" and (scope.get("path") == "/mcp" or scope.get("path", "").startswith("/mcp/")):
        await session_manager.handle_request(scope, receive, send); return
    await fapp(scope, receive, send)


@fapp.get("/.well-known/agent-card.json", openapi_extra={"security": []})
@fapp.get("/.well-known/agent.json", include_in_schema=False, openapi_extra={"security": []})
async def agent_card():
    """Agent card — how a buying agent learns what this sells and what it costs.

    Added 2026-07-26: the borisinc.com surface audit found mem.borisinc.com had
    neither an agent card nor /.well-known/x402, so an agent discovering it by
    the conventional well-known paths learned nothing about a service that sells.
    Every skill states the free/paid pairing explicitly.
    """
    return {
        "protocolVersion": "1.0",
        "name": "Boris Memory",
        "description": ("Persistent, semantically-indexed memory for AI agents. Your wallet is your private "
                        "namespace. Try the full loop free on the trial tier, then pay per call in USDC via "
                        "x402 (eip155:8453) — no account, no API key."),
        "url": "https://mem.borisinc.com",
        "provider": {"organization": "Boris Inc", "url": "https://borisinc.com"},
        "version": "1.0.0",
        "documentationUrl": "https://mem.borisinc.com/llms.txt",
        "capabilities": {"x402": True, "streaming": False, "networks": [NETWORK],
                         "mcp": "https://mem.borisinc.com/mcp"},
        "skills": [
            {"id": "memory_write", "name": "Write a memory",
             "description": ("Store a record in your wallet-private namespace, embedded for semantic recall. "
                             "GET /trial/memory/write — FREE (5 records, 24h, not private). "
                             "GET /paid/memory/write — $0.001 via x402."),
             "tags": ["memory", "storage", "embeddings"]},
            {"id": "memory_search", "name": "Semantic search",
             "description": ("Semantic search over your namespace. "
                             "GET /trial/memory/search — FREE on the trial tier. "
                             "GET /paid/memory/search — $0.002 via x402."),
             "tags": ["memory", "search", "semantic"]},
            {"id": "memory_export", "name": "Export namespace",
             "description": "Export everything in your namespace. GET /paid/memory/export — $0.01 via x402.",
             "tags": ["memory", "export", "portability"]},
        ],
        "pricing": {**PRICES, "subscription": "$2.00 / 30 days, unlimited"},
        "freeEndpoints": ["/trial/memory/write", "/trial/memory/search", "/memory/stats",
                          "/grant/payload", "/llms.txt", "/quickstart", "/openapi.json", "/health"],
    }


@fapp.get("/.well-known/x402", openapi_extra={"security": []})
async def wk_x402():
    """Machine-readable list of payable resources — what an x402 buyer parses first."""
    _entries = [("https://mem.borisinc.com" + path, price,
                 "memory " + path.rsplit("/", 1)[-1], "GET")
                for path, price in HTTP_PRICES.items()]
    _mcp_entries = [("mcp://tool/" + t, p, "memory-mcp " + t, "TOOL")
                    for t, p in PRICES.items()]
    return {"x402Version": 2, "service": "memory-mcp",
            "items": _x402_items(_entries + _mcp_entries, NETWORK, PAY_TO),
            "mcp": "https://mem.borisinc.com/mcp",
            "resources": [{"url": "https://mem.borisinc.com" + path, "method": "GET",
                           "price": price, "network": NETWORK, "payTo": PAY_TO,
                           "description": "memory " + path.rsplit("/", 1)[-1]}
                          for path, price in HTTP_PRICES.items()],
            "free": ["/trial/memory/write", "/trial/memory/search", "/memory/stats"],
            "subscription": {"url": "https://mem.borisinc.com/subscribe", "price": "$2.00", "period_days": 30},
            "guarantee": "your wallet is your namespace — no account, no API key"}


@fapp.get("/.well-known/agent-registration.json")
async def agent_registration_json():
    """ERC-8004 registration file (spec registration-v1)."""
    from pathlib import Path as _P
    import json as _json
    p = _P(__file__).parent / "agent-registration.json"
    if p.exists():
        return JSONResponse(_json.loads(p.read_text()))
    return JSONResponse({"error": "not registered yet"}, status_code=404)


# ---------- NEW: subscription, trial tier, grant helper ----------
from x402.http.utils import encode_payment_required_header, decode_payment_signature_header

@fapp.get("/subscribe")
async def subscribe(request: Request):
    """PAID $2.00: 30 days of unlimited write/search/export for the paying wallet's namespace.
    Returns a bearer token (Authorization: Bearer mem_...)."""
    reqs = rs.build_payment_requirements(ResourceConfig(scheme="exact", network=NETWORK, pay_to=PAY_TO, price=SUB_PRICE))
    pay_hdr = request.headers.get("x-payment") or request.headers.get("payment-signature")
    if not pay_hdr:
        pr = rs.create_payment_required_response(reqs, error=f"memory-mcp subscription: {SUB_PRICE} for {SUB_DAYS} days unlimited memory ops (your wallet namespace)")
        # x402scan requires resource + bazaar input schema in the 402 requirements (was the 1 rejected
        # listing of 11); the manual route bypasses payment_middleware, so inject them post-hoc.
        import base64 as _b64
        _d = json.loads(_b64.b64decode(encode_payment_required_header(pr)))
        _d["resource"] = {"url": "https://mem.borisinc.com/subscribe",
                          "description": f"{SUB_PRICE} for {SUB_DAYS} days unlimited memory ops, wallet-bound bearer token", "mimeType": "application/json"}
        _d["extensions"] = declare_discovery_extension(
            input={"type": "http", "method": "GET"},
            output=OutputConfig(example={"token": "mem_...", "wallet": "0xabc...", "expires": 1789000000, "days": SUB_DAYS}))
        _bz = _d["extensions"].get("bazaar", {}).get("info", {}).get("input")
        if isinstance(_bz, dict) and "method" not in _bz:
            _bz["method"] = "GET"
        _hdr = _b64.b64encode(json.dumps(_d).encode()).decode()
        return JSONResponse({"price": SUB_PRICE, "days": SUB_DAYS,
                             "covers": list(HTTP_PRICES), "note": "wallet-bound; token in response after payment"},
                            status_code=402, headers={"payment-required": _hdr})
    try:
        payload = decode_payment_signature_header(pay_hdr)
        vr = rs.verify_payment(payload, reqs[0])
        if not getattr(vr, "is_valid", False):
            return JSONResponse({"error": "payment invalid"}, status_code=402)
        d = payload.model_dump(by_alias=True) if hasattr(payload, "model_dump") else dict(payload)
        def dig(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("from", "from_", "sender") and isinstance(v, str) and v.startswith("0x"): return v
                    r = dig(v)
                    if r: return r
        wallet = (dig(d) or "").lower()
        if not VALID_NS.match(wallet):
            return JSONResponse({"error": "payer wallet could not be determined — not charged"}, status_code=400)
        sr = rs.settle_payment(payload, reqs[0])
        if not getattr(sr, "success", False):
            return JSONResponse({"error": "settlement failed — not charged"}, status_code=502)
        import secrets
        tok = "mem_" + secrets.token_hex(16)
        exp = time.time() + SUB_DAYS * 86400
        c = _subs(); c.execute("INSERT INTO mem_subs(token,wallet,created,expires) VALUES(?,?,?,?)",
                               (tok, wallet, time.time(), exp)); c.commit(); c.close()
        return {"token": tok, "wallet": wallet, "expires": int(exp), "days": SUB_DAYS,
                "usage": "send header  Authorization: Bearer " + tok,
                "settle_tx": getattr(sr, "transaction", None)}
    except Exception as e:
        return JSONResponse({"error": f"payment processing failed: {str(e)[:120]}"}, status_code=502)


# ---- FREE trial: taste the full loop with zero payment (IP-keyed, capped, ephemeral, NOT private) ----
TRIAL_MAX, TRIAL_TTL = 5, 24 * 3600


def _real_ip(request):
    # Trust forwarded headers only when the direct peer is loopback (cloudflared).
    # Any other peer could spoof X-Forwarded-For, so use the socket peer itself.
    peer = request.client.host if request.client else "?"
    if peer in ("127.0.0.1", "::1"):
        cf = request.headers.get("cf-connecting-ip")
        if cf:
            return cf.strip()
        xff = [x.strip() for x in request.headers.get("x-forwarded-for", "").split(",") if x.strip()]
        if xff:
            return xff[-1]
    return peer

def _trial_ns(request):
    import hashlib
    ip = _real_ip(request)
    return "trial:" + hashlib.sha1(ip.encode()).hexdigest()[:12]

def _trial_gc():
    with contextlib.suppress(Exception):
        c = _mem(); c.execute("DELETE FROM memories WHERE ns LIKE 'trial:%' AND ts<?", (time.time() - TRIAL_TTL,)); c.commit(); c.close()

@fapp.get("/trial/memory/write", openapi_extra={"security": []})
async def trial_write(request: Request, content: str, tags: str = ""):
    _trial_gc()
    ns = _trial_ns(request)
    c = _mem(); n = c.execute("SELECT COUNT(*) FROM memories WHERE ns=?", (ns,)).fetchone()[0]; c.close()
    if n >= TRIAL_MAX:
        return JSONResponse({"error": f"trial cap reached ({TRIAL_MAX} records/24h)",
                             "upgrade": "pay per call via x402 ($0.001/write) or /subscribe ($2.00/30d) — your wallet becomes a private namespace"},
                            status_code=402)
    out = await asyncio.to_thread(t_write, {"content": content, "tags": [t for t in tags.split(",") if t]}, ns)
    out["trial"] = True
    out["warning"] = "trial namespaces are IP-derived, capped, auto-deleted after 24h and NOT private — use a wallet for real memory"
    return out

@fapp.get("/trial/memory/search", openapi_extra={"security": []})
async def trial_search(request: Request, query: str, k: int = 5):
    out = await asyncio.to_thread(t_search, {"query": query, "k": k}, _trial_ns(request))
    out["trial"] = True
    return out


# ---- FREE helper: EIP-712 grant payload for shared namespaces ----
@fapp.get("/grant/payload", openapi_extra={"security": []})
async def grant_payload(namespace: str, grantee: str, mode: str = "read", days: int = 30):
    if mode not in ("read", "write", "rw"):
        return JSONResponse({"error": "mode must be read|write|rw"}, status_code=400)
    days = max(1, min(days, GRANT_MAX_DAYS))
    exp = int(time.time()) + days * 86400
    td = _grant_typed(namespace.lower(), grantee.lower(), mode, exp)
    return {"typedData": td, "expires": exp,
            "instructions": ("namespace owner signs this with eth_signTypedData_v4 (EOA), then base64-encode "
                             "{namespace,grantee,mode,expires,signature} and the grantee passes it as grant= with on_behalf= on paid calls")}


# ---------------------------------------------------------------------------
# Method negotiation (added 2026-07-27, attended — settle audit).
# FastAPI does NOT auto-add HEAD to GET routes the way plain Starlette does, so
# every route answered `405 allow: GET` to HEAD, including /.well-known/x402 —
# the manifest trust indexers HEAD to decide whether we speak x402 at all.
# Registered last => outermost: rewrite lands before routing and before the
# paywall, which still issues its normal 402 for unpaid HEADs.
# ---------------------------------------------------------------------------
@fapp.middleware("http")
async def head_support(request: Request, call_next):
    if request.method != "HEAD":
        return await call_next(request)
    from fastapi.responses import Response
    request.scope["method"] = "GET"
    request.scope["_orig_method"] = "HEAD"
    resp = await call_next(request)
    body = b""
    it = getattr(resp, "body_iterator", None)
    if it is not None:
        with contextlib.suppress(Exception):
            async for chunk in it:
                body += chunk
    else:
        body = getattr(resp, "body", b"") or b""
    headers = dict(resp.headers)
    headers.pop("transfer-encoding", None)
    headers["content-length"] = str(len(body))
    return Response(status_code=resp.status_code, headers=headers)
