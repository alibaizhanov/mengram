"""`mengram local …` — the folder from the command line.

Kept out of cli.py so the top-level file does not grow another thousand
lines. cli.py registers `add_parser` and dispatches to `run`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import (config_path, describe_model, llm_client, memory_dir, read_config,
                     write_config)
from .store import LocalStore


def _root(args) -> Path:
    root = memory_dir(getattr(args, "memory", None))
    if root is None:
        root = Path("memory")
    return root


def _open(args) -> LocalStore:
    root = _root(args)
    if not root.is_dir():
        print(f"no memory folder at {root} — run: mengram local init {root}", file=sys.stderr)
        sys.exit(1)
    return LocalStore(root)


def cmd_init(args) -> int:
    root = Path(args.dir).expanduser() if args.dir else _root(args)
    root.mkdir(parents=True, exist_ok=True)
    cfg = read_config(root)
    provider = (args.provider or "").strip().lower()
    if provider:
        llm = {"provider": provider}
        if provider in ("anthropic", "openai"):
            block = {}
            if args.api_key:
                block["api_key"] = args.api_key
            if args.model:
                block["model"] = args.model
            if not block.get("api_key"):
                print(f"note: no --api-key given; {provider.upper()}_API_KEY from the environment "
                      "will be used at run time", file=sys.stderr)
            llm[provider] = block
        elif provider == "ollama":
            llm["ollama"] = {"model": args.model or "llama3.1:8b"}
            if args.base_url:
                llm["ollama"]["base_url"] = args.base_url
        elif provider == "mock":
            pass
        else:
            print(f"unknown provider: {provider} (anthropic | openai | ollama)", file=sys.stderr)
            return 1
        cfg["llm"] = llm
    write_config(root, cfg)
    store = LocalStore(root)
    store.save()
    print(f"memory folder: {root.resolve()}")
    print(f"model: {describe_model(root)}")
    print(f"config: {config_path(root)}")
    print("")
    print("next:")
    print(f"  export MENGRAM_MEMORY_DIR={root.resolve()}")
    print("  mengram local add \"I deploy to Railway from main; last time the pool was cold\"")
    print("  mengram local search \"railway\"")
    print("  mengram hook install        # Claude Code hooks, all local")
    return 0


def cmd_add(args) -> int:
    store = _open(args)
    text = sys.stdin.read() if args.stdin else " ".join(args.text or [])
    if not text.strip():
        print("nothing to add (pass text, or --stdin)", file=sys.stderr)
        return 1
    client = llm_client(store.root)
    if client is None:
        print("no model configured — extraction needs one.\n"
              "  mengram local init --provider anthropic --api-key sk-ant-...   (or openai / ollama)\n"
              "  or export ANTHROPIC_API_KEY / OPENAI_API_KEY", file=sys.stderr)
        return 2
    try:
        conversation = json.loads(text) if text.lstrip().startswith("[") else text
    except json.JSONDecodeError:
        conversation = text
    stats = store.add(conversation, client)
    procs = stats["procedures"]
    print(f"entities +{stats['entities_created']} (updated {stats['entities_updated']}), "
          f"facts +{stats['facts_added']}, episodes +{stats['episodes_saved']}, "
          f"procedures created {procs['created']} / refreshed {procs['refreshed']} / kept {procs['kept']}")
    return 0


def cmd_search(args) -> int:
    store = _open(args)
    hits = store.search(" ".join(args.query), limit=args.limit)
    if not hits:
        print("nothing relevant")
        return 1
    for h in hits:
        if h["type"] == "entity":
            print(f"{h['entity']}" + (f" ({h['entity_type']})" if h.get("entity_type") else ""))
            for f in h["facts"][:5]:
                print(f"  - {f}")
        elif h["type"] == "episode":
            when = f"{h['happened']}: " if h.get("happened") else ""
            print(f"episode {when}{h['summary']}" + (f" → {h['outcome']}" if h.get("outcome") else ""))
        else:
            print(f"workflow {h['name']} (v{h['version']}, {h['reliability']})")
    return 0


def cmd_procedures(args) -> int:
    store = _open(args)
    procs = store.procedures(query=" ".join(args.query) if args.query else None, limit=args.limit)
    if not procs:
        print("no procedures yet")
        return 1
    for p in procs:
        print(f"{p['name']}  v{p['version']}  {p['success_count']}✓/{p['fail_count']}✗  {p['reliability']}")
        if p.get("trigger_condition"):
            print(f"  when: {p['trigger_condition']}")
        for i, s in enumerate(p["steps"], 1):
            rec = f"  ({s['success_count']}✓/{s['fail_count']}✗)" if "success_count" in s else ""
            print(f"  {i}. {s['action']}" + (f" — {s['detail']}" if s.get("detail") else "") + rec)
        if p.get("last_failure"):
            when = f"{p['last_failed']}: " if p.get("last_failed") else ""
            print(f"  last failure: {when}{p['last_failure']}")
    return 0


def cmd_feedback(args) -> int:
    store = _open(args)
    success = bool(args.success)
    result = store.procedure_feedback(args.name, success=success, failed_at_step=args.step,
                                      reason=args.context if not success else None)
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    print(f"{result['name']}  v{result['version']}  {result['success_count']}✓/{result['fail_count']}✗  "
          f"{result['reliability']}")
    if success or not args.context:
        return 0
    client = llm_client(store.root)
    if client is None:
        print("failure recorded; no model configured, so the workflow was not revised "
              "(set one to let a failure produce a new version)", file=sys.stderr)
        return 0
    from .evolve import evolve_on_failure
    r = evolve_on_failure(store, args.name, args.context, client, failed_at_step=args.step)
    status = r.get("status")
    if status == "promoted":
        print(f"revised → v{r['version']} ({r['reliability']}); violated assumption: {r.get('violated_assumption')}")
    elif status == "revised_in_place":
        print(f"revised in place (v{r['version']} had never run); violated assumption: {r.get('violated_assumption')}")
    elif status == "quarantined":
        names = ", ".join(x["dependent_name"] for x in r["regressions"])
        print(f"revision QUARANTINED — it would break: {names}. See {r['quarantine']}")
    elif status == "no_change":
        print(f"no revision: {r.get('reason')}")
    else:
        print(f"revision failed: {r.get('error')}", file=sys.stderr)
        return 1
    return 0


def cmd_stat(args) -> int:
    store = _open(args)
    s = store.stats()
    print(f"{s['entities']} entities ({s['facts']} facts), {s['episodes']} episodes, "
          f"{s['procedures']} procedures ({s['procedures_with_record']} with a record)")
    print(f"model: {describe_model(store.root)}")
    from .evolve import read_quarantine
    q = read_quarantine(store)
    if q:
        print(f"{len(q)} quarantined revision(s) waiting for review — mengram local quarantine")
    return 0


def cmd_quarantine(args) -> int:
    store = _open(args)
    from .evolve import read_quarantine
    q = read_quarantine(store)
    if not q:
        print("nothing quarantined")
        return 0
    for i, e in enumerate(q, 1):
        names = ", ".join(r["dependent_name"] for r in e.get("regressions", []))
        print(f"{i}. {e['procedure']} v{e['version']} ({e['date']}): {e['reason']}")
        print(f"   would break: {names}")
        if e.get("violated_assumption"):
            print(f"   violated assumption: {e['violated_assumption']}")
        for j, s in enumerate(e.get("proposed_steps", []), 1):
            print(f"   {j}. {s['action']}" + (f" — {s['detail']}" if s.get("detail") else ""))
    return 0


def add_parser(sub) -> None:
    p = sub.add_parser("local", help="Memory in a folder you own — no account, no server")
    lsub = p.add_subparsers(dest="local_action", required=True)

    def common(sp):
        sp.add_argument("--memory", default=None, help="memory folder (default: $MENGRAM_MEMORY_DIR or ./memory)")

    sp = lsub.add_parser("init", help="create a memory folder and choose a model")
    sp.add_argument("dir", nargs="?", default=None)
    sp.add_argument("--provider", default=None, help="anthropic | openai | ollama")
    sp.add_argument("--api-key", default=None, dest="api_key")
    sp.add_argument("--model", default=None)
    sp.add_argument("--base-url", default=None, dest="base_url", help="Ollama URL")
    common(sp); sp.set_defaults(local_func=cmd_init)

    sp = lsub.add_parser("add", help="extract from text (or a JSON conversation) with your model")
    sp.add_argument("text", nargs="*")
    sp.add_argument("--stdin", action="store_true")
    common(sp); sp.set_defaults(local_func=cmd_add)

    sp = lsub.add_parser("search", help="facts, events and workflows that match")
    sp.add_argument("query", nargs="+")
    sp.add_argument("--limit", type=int, default=5)
    common(sp); sp.set_defaults(local_func=cmd_search)

    sp = lsub.add_parser("procedures", help="learned workflows with their record")
    sp.add_argument("query", nargs="*")
    sp.add_argument("--limit", type=int, default=20)
    common(sp); sp.set_defaults(local_func=cmd_procedures)

    sp = lsub.add_parser("feedback", help="record a run of a workflow")
    sp.add_argument("name")
    g = sp.add_mutually_exclusive_group(required=True)
    g.add_argument("--success", action="store_true")
    g.add_argument("--failure", action="store_true")
    sp.add_argument("--step", type=int, default=None, help="1-based step that failed")
    sp.add_argument("--context", default=None, help="what happened; with --failure, asks your model to revise")
    common(sp); sp.set_defaults(local_func=cmd_feedback)

    sp = lsub.add_parser("stat", help="what is in the folder")
    common(sp); sp.set_defaults(local_func=cmd_stat)

    sp = lsub.add_parser("quarantine", help="revisions the regression gate refused")
    common(sp); sp.set_defaults(local_func=cmd_quarantine)


def run(args) -> int:
    return int(args.local_func(args) or 0)
