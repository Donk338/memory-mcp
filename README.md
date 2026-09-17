# memory-mcp — persistent memory for AI agents, paid per call via x402

Persistent, semantically-searchable memory for AI agents, exposed over the
Model Context Protocol (MCP) and plain HTTP, paid per call via
[x402](https://www.x402.org/) in USDC on Base mainnet. There are no accounts
and no API keys — your wallet address **is** your namespace, so any agent that
can sign an x402 payment can write and recall memory across sessions, hosts,
and frameworks. A free trial tier and owner-signed EIP-712 grants for sharing
a namespace across agents are described below.

**mem.borisinc.com/mcp** (streamable HTTP MCP) · Base mainnet USDC · facilitator: xpay

Your wallet is your memory: every paid call is namespaced to the paying wallet.
No accounts, no API keys — pay $0.001 and your agent has permanent, semantically
searchable memory across sessions, hosts, and frameworks.

| tool | price | what |
|---|---|---|
| memory_write | $0.001 | store text (≤8k chars, tags), semantic-indexed |
| memory_search | $0.002 | top-k cosine search over your namespace |
| memory_export | $0.01 | full dump of your namespace (paginated, ≤1000/call via offset) |
| memory_forget | $0.001 | delete memories by id (≤100/call) |
| memory_import | $0.01 | bulk-write into your namespace (≤200/call, accepts memory_export's shape) |
| memory_stats | free | count + last-write ts only (never content) |

## Quickstart

Full walkthrough, including a free no-wallet trial (5 records, 24h, IP-scoped,
not private) you can hit before bringing a wallet:
**https://mem.borisinc.com/quickstart**

MCP endpoint (streamable HTTP): **https://mem.borisinc.com/mcp**

## Shared namespaces (EIP-712 grants)

A namespace owner can delegate read/write/rw access to another agent by
signing an EIP-712 `Grant` (namespace, grantee, mode, expires) — build the
payload with `GET /grant/payload?namespace=&grantee=&mode=read|write|rw&days=30`,
then have the grantee attach `on_behalf=<owner>&grant=<base64 blob>` to its own
paid calls. Grants are capped at 90 days and use EOA signatures in v1 — this
lets, for example, a research agent write into a shared namespace that an
execution agent reads from, without ever handing over the owner's key.

Namespaces also support ERC-8004 agent-keyed addressing (`agent:8453:<id>`),
so memory can survive wallet rotation for a registered on-chain agent.

## Free wallet-keyed tier (zero payment, real memory)

An agent gets real, private, persistent memory with zero payment by proving
wallet ownership instead of paying: `GET /free/memory/write` and
`GET /free/memory/search` take header `X-Wallet-Auth: <base64 JSON
{address,ts,signature}>` where `signature` is an EIP-191 `personal_sign` of
`borismem-free:<address>:<ts>` (ts within 600s). MCP tools `memory_write_free`
/ `memory_search_free` take the same base64 blob as a `wallet_auth` arg. This
writes into the *same* namespace your paid calls use — the free tier fills
the real memory, not a side namespace. Capped at 1000 writes + 500 searches
per wallet per rolling 30 days (counted from memory-calls.db); over cap
returns 402 with a pointer to the paid tier or `/subscribe`. The IP-keyed
`/trial/*` tier (5 records, 24h, not private) is unchanged and still exists
for agents that haven't got a wallet yet.

## MCP registry

Registered on the MCP registry as **`com.borisinc/memory`**
(`https://mem.borisinc.com/mcp`, streamable-http).

Ops: `memory-mcp.service` :8407, ledger memory-calls.db, store memories.db,
embeddings via the local GPU embedder (1024-dim). Test client: mcp_buyer.py.
