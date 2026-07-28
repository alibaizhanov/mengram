#!/usr/bin/env python3
"""
Give your Kimi K3 agent long-term memory.

Kimi K3 (Moonshot) is a great agent brain, but like every LLM it starts every
session amnesiac — it re-derives your project, your preferences, and the
mistakes it already made. This wires Mengram (a memory layer) into a Kimi agent
as tools, so it remembers across sessions: facts about you, and procedures it
learned from past failures.

Kimi K3 speaks the OpenAI API, so this is a drop-in: same `openai` client, a
different base_url. Mengram is multilingual (works in Chinese + English), which
matters for a Kimi-first stack.

Run:
  # Real: needs a Moonshot key (platform.moonshot.ai) + a free Mengram key (mengram.io)
  export MOONSHOT_API_KEY=...   MENGRAM_API_KEY=om-...
  python kimi_agent.py "what do you know about my deploy setup?"

  # Offline: no keys, canned walkthrough of the memory loop
  python kimi_agent.py --offline

Built against Moonshot's documented OpenAI-compatible API (base_url
https://api.moonshot.ai/v1, model kimi-k3). Verify current model id/pricing at
platform.moonshot.ai before production use.
"""
import argparse
import json
import os
import sys

KIMI_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-k3")
USER_ID = os.environ.get("MENGRAM_USER_ID", "kimi-demo")


# --- Mengram memory, exposed to the agent as tools --------------------------
def _mem():
    from mengram import Mengram
    return Mengram(api_key=os.environ["MENGRAM_API_KEY"])


def search_memory(query: str) -> str:
    results = _mem().search(query, user_id=USER_ID, limit=5)
    if not results:
        return "No memories found."
    return "\n".join(
        f"{r.get('entity','?')}: {'; '.join(r.get('facts', [])[:5])}" for r in results
    )


def save_memory(content: str) -> str:
    _mem().add_text(content, user_id=USER_ID, source="kimi-agent")
    return "Saved."


def get_procedures(task: str) -> str:
    procs = _mem().procedures(query=task, limit=5, user_id=USER_ID)
    if not procs:
        return "No learned procedures for this task."
    out = []
    for p in procs:
        pre = (p.get("metadata") or {}).get("preconditions") or []
        out.append(
            f"{p.get('name','?')} (v{p.get('version',1)}, "
            f"{p.get('success_count',0)} ok / {p.get('fail_count',0)} failed)"
            + (f" — verify first: {pre}" if pre else "")
        )
    return "\n".join(out)


TOOLS = [
    {"type": "function", "function": {
        "name": "search_memory",
        "description": "Search the user's long-term memory for facts, preferences, and past events.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to look for"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "save_memory",
        "description": "Save a durable fact or decision to long-term memory.",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string"}}, "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "get_procedures",
        "description": "Retrieve learned workflows for a task, with their success/failure track record.",
        "parameters": {"type": "object", "properties": {
            "task": {"type": "string"}}, "required": ["task"]}}},
]
_DISPATCH = {"search_memory": search_memory, "save_memory": save_memory, "get_procedures": get_procedures}


def run_real(prompt: str):
    from openai import OpenAI  # Kimi speaks the OpenAI API
    client = OpenAI(base_url=KIMI_BASE_URL, api_key=os.environ["MOONSHOT_API_KEY"])
    messages = [
        {"role": "system", "content": "You are a coding assistant with long-term memory. "
         "Before answering anything that depends on the user or past work, call search_memory "
         "or get_procedures. Save durable new facts with save_memory."},
        {"role": "user", "content": prompt},
    ]
    for _ in range(6):
        resp = client.chat.completions.create(model=KIMI_MODEL, messages=messages, tools=TOOLS)
        msg = resp.choices[0].message
        if not msg.tool_calls:
            print("\nKimi:", msg.content)
            return
        messages.append(msg)
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = _DISPATCH[tc.function.name](**args)
            print(f"  [memory] {tc.function.name}({args}) -> {result[:80]}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
    print("(stopped after 6 tool rounds)")


def run_offline():
    print("""
Offline walkthrough — Kimi K3 agent + Mengram memory
====================================================
User: "deploy the app the way we always do"

  Kimi calls get_procedures("deploy")
  [memory] -> deploy-railway (v3, 11 ok / 1 failed) — verify first:
             ["run migrations before push"]

  Kimi: "Based on your learned procedure (v3, failed once in April on
  migrations): I'll run migrations first, then push. Deploying now."

Without memory, Kimi would re-derive the deploy from scratch and could
repeat the migration mistake. With Mengram wired in as tools, it recalls
the versioned procedure and its precondition. Same for facts across
sessions — in English or Chinese.

Run for real with MOONSHOT_API_KEY + MENGRAM_API_KEY set.
""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="?", default="")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    if args.offline or not (os.environ.get("MOONSHOT_API_KEY") and os.environ.get("MENGRAM_API_KEY")):
        if not args.offline:
            print("(no MOONSHOT_API_KEY / MENGRAM_API_KEY set — showing offline walkthrough)\n")
        run_offline()
        return
    if not args.prompt:
        print("Usage: python kimi_agent.py \"your prompt\"")
        return
    run_real(args.prompt)


if __name__ == "__main__":
    main()
