"""HTTP x402 test buyer for memory-mcp."""
import uuid, httpx
from eth_account import Account
from x402 import x402ClientSync
from x402.http import x402HTTPClientSync, PaymentRoundTripper
from x402.mechanisms.evm import EthAccountSigner
from x402.mechanisms.evm.exact import ExactEvmScheme

env = dict(l.strip().split("=", 1) for l in open("/home/donk/aegis/agent-test.env") if "=" in l)
acct = Account.from_key(env["AGENT_TEST_KEY"])
print("wallet:", acct.address, flush=True)
client = x402ClientSync()
client.register("eip155:8453", ExactEvmScheme(EthAccountSigner(acct)))
rt = PaymentRoundTripper(x402HTTPClientSync(client))

def buy(url):
    with httpx.Client(timeout=90) as s:
        r = s.get(url)
        final = rt.handle_response(str(uuid.uuid4()), r.status_code, dict(r.headers), r.content,
                                   lambda hdrs: s.get(url, headers=hdrs))
        code = getattr(final, "status_code", r.status_code)
        print(f"{code} <- {url[:70]}", flush=True)
        print("   ", getattr(final, "text", "")[:200], flush=True)

buy("http://localhost:8407/paid/memory/write?content=HTTP surface test: memory-mcp now sells over plain HTTP too, added 2026-07-06.&tags=http,launch")
buy("http://localhost:8407/paid/memory/search?query=does memory-mcp support plain HTTP?")
