"""memory-mcp test buyer (streamable-http). Usage: mcp_buyer.py [url]"""
import asyncio, sys
from eth_account import Account
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from x402 import x402Client
from x402.mcp.client import x402MCPSession
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact import ExactEvmScheme

env = dict(l.strip().split("=", 1) for l in open("/home/donk/aegis/agent-test.env") if "=" in l)
acct = Account.from_key(env["AGENT_TEST_KEY"])
print("buyer wallet:", acct.address, flush=True)
URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8407/mcp"

async def main():
    xc = x402Client()
    xc.register("eip155:8453", ExactEvmScheme(EthAccountSigner(acct)))
    async with streamablehttp_client(URL) as (r, w, _):
        async with ClientSession(r, w) as session:
            await session.initialize()
            x = x402MCPSession(session, xc)
            r1 = await x.call_tool("memory_write", {"content": "memory-mcp launch test: Dave's agent memory service went live 2026-07-06 on mem.borisinc.com.", "tags": ["launch", "test"]})
            print("write paid:", r1.payment_made, "->", r1.content[0].text if r1.content else r1, flush=True)
            r2 = await x.call_tool("memory_search", {"query": "when did the memory service launch?"})
            print("search paid:", r2.payment_made, "->", (r2.content[0].text if r2.content else "")[:300], flush=True)
            r3 = await x.call_tool("memory_stats", {"namespace": acct.address})
            print("stats free ->", r3.content[0].text if r3.content else r3, flush=True)

asyncio.run(main())
