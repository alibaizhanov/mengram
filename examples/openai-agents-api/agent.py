"""Per-user memory for a product built on the OpenAI Agents API / Agents SDK.

OpenAI's Agents API (public beta, Sept 2026) gives every agent a durable
session, auto-compaction and a sandbox memory — per *workspace*. A product
with returning end users needs memory per *user*: what this customer
prefers, what was decided with them, what broke last time. Mengram is that
memory, attached as an MCP server; one connection per end user, scoped by a
header, so the model never has to remember to pass a user id.

Run:
    pip install openai-agents
    MENGRAM_API_KEY=om-... OPENAI_API_KEY=sk-... python agent.py cust_1042 "what did we agree about delivery?"
"""
import asyncio
import os
import sys

from agents import Agent, Runner
from agents.mcp import MCPServerStreamableHttp

MENGRAM_MCP = os.environ.get("MENGRAM_MCP_URL", "https://mengram.io/mcp")
MENGRAM_KEY = os.environ["MENGRAM_API_KEY"]


async def run_for(end_user_id: str, message: str) -> str:
    async with MCPServerStreamableHttp(
        name="mengram",
        params={
            "url": MENGRAM_MCP,
            "headers": {
                "Authorization": f"Bearer {MENGRAM_KEY}",
                # Everything this connection remembers or recalls belongs to
                # this end user. Isolated from every other user of your product.
                "X-Mengram-User": end_user_id,
            },
            "timeout": 15,
        },
        cache_tools_list=True,
    ) as memory:
        agent = Agent(
            name="Support",
            instructions=(
                "You help customers of a car-rental company. Before answering, "
                "call `context_for` with the customer's request to load what is "
                "known about them (max_tokens 600). After the conversation, call "
                "`remember` with anything durable: preferences, decisions, what "
                "went wrong. Do not store small talk."
            ),
            mcp_servers=[memory],
        )
        result = await Runner.run(agent, message)
        return result.final_output


if __name__ == "__main__":
    user, msg = sys.argv[1], " ".join(sys.argv[2:])
    print(asyncio.run(run_for(user, msg)))
