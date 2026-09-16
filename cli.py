"""
Mengram CLI

Usage:
    mengram init              # Interactive setup
    mengram init --provider anthropic --api-key sk-ant-...
    mengram server            # Start MCP server
    mengram server --config ~/.mengram/config.yaml
    mengram status            # Check setup
    mengram stats             # Vault statistics
"""

import os
import sys
import json
import yaml
import shutil
import platform
import argparse
from pathlib import Path


# Default paths
DEFAULT_HOME = Path.home() / ".mengram"
DEFAULT_CONFIG = DEFAULT_HOME / "config.yaml"
DEFAULT_VAULT = DEFAULT_HOME / "vault"


def get_claude_desktop_config_path() -> Path:
    """Path to Claude Desktop MCP config"""
    system = platform.system()
    if system == "Darwin":  # macOS
        return Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    elif system == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Claude" / "claude_desktop_config.json"
    else:  # Linux
        return Path.home() / ".config" / "Claude" / "claude_desktop_config.json"


def cmd_init(args):
    """Interactive setup — creates config, vault, MCP integration"""
    print("🧠 Mengram Setup\n")

    home_dir = Path(args.home) if args.home else DEFAULT_HOME
    home_dir.mkdir(parents=True, exist_ok=True)

    config_path = home_dir / "config.yaml"
    vault_path = home_dir / "vault"

    # --- 1. LLM Provider ---
    provider = args.provider
    api_key = args.api_key

    if not provider:
        print("Which LLM provider?")
        print("  1) anthropic  (Claude — recommended)")
        print("  2) openai     (GPT)")
        print("  3) ollama     (local, free)")
        choice = input("\nChoice [1]: ").strip() or "1"
        provider = {"1": "anthropic", "2": "openai", "3": "ollama"}.get(choice, "anthropic")

    if not api_key and provider in ("anthropic", "openai"):
        env_var = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
        env_key = os.environ.get(env_var, "")

        if env_key:
            print(f"\n✅ Found {env_var} in environment")
            api_key = env_key
        else:
            api_key = input(f"\n🔑 Enter your API key: ").strip()
            if not api_key:
                print("❌ API key required. Set it later in config.yaml")
                api_key = "YOUR_API_KEY_HERE"

    # --- 2. Vault path ---
    if args.vault:
        vault_path = Path(args.vault)
    else:
        default_display = str(vault_path)
        custom = input(f"\n📁 Vault path [{default_display}]: ").strip()
        if custom:
            vault_path = Path(custom)

    vault_path.mkdir(parents=True, exist_ok=True)

    # --- 3. Write config ---
    config = {
        "vault_path": str(vault_path),
        "llm": {
            "provider": provider,
        },
        "semantic_search": {
            "enabled": True,
        },
    }

    if provider == "anthropic":
        config["llm"]["anthropic"] = {
            "api_key": api_key,
            "model": "claude-sonnet-4-20250514",
        }
    elif provider == "openai":
        config["llm"]["openai"] = {
            "api_key": api_key,
            "model": "gpt-4o-mini",
        }
    elif provider == "ollama":
        config["llm"]["ollama"] = {
            "base_url": "http://localhost:11434",
            "model": "llama3.2",
        }

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    print(f"\n✅ Config: {config_path}")

    # --- 4. Create run_server.sh ---
    server_script = home_dir / "run_server.sh"
    python_path = sys.executable

    script_content = f"""#!/bin/bash
cd "{home_dir}"
"{python_path}" -m api.mcp_server "{config_path}"
"""
    with open(server_script, "w") as f:
        f.write(script_content)
    os.chmod(server_script, 0o755)
    print(f"✅ Server script: {server_script}")

    # --- 5. Find package location for MCP ---
    try:
        import mengram
        package_dir = Path(mengram.__file__).parent
        # If installed as package, the engine/ and api/ are siblings
        # We need the directory containing api/mcp_server.py
        if (package_dir / "api" / "mcp_server.py").exists():
            mcp_working_dir = str(package_dir)
        elif (package_dir.parent / "api" / "mcp_server.py").exists():
            mcp_working_dir = str(package_dir.parent)
        else:
            mcp_working_dir = str(home_dir)
    except ImportError:
        mcp_working_dir = str(home_dir)

    # --- 6. Claude Desktop MCP integration ---
    claude_config_path = get_claude_desktop_config_path()
    setup_mcp = True

    if not args.no_mcp:
        if not claude_config_path.parent.exists():
            print(f"\n⚠️  Claude Desktop config dir not found: {claude_config_path.parent}")
            print("   Install Claude Desktop first, then run: mengram init --mcp-only")
            setup_mcp = False
        else:
            # Read existing config
            claude_config = {}
            if claude_config_path.exists():
                try:
                    with open(claude_config_path) as f:
                        claude_config = json.load(f)
                except (json.JSONDecodeError, Exception):
                    claude_config = {}

            # Add MCP server
            if "mcpServers" not in claude_config:
                claude_config["mcpServers"] = {}

            claude_config["mcpServers"]["mengram"] = {
                "command": str(server_script),
            }

            with open(claude_config_path, "w") as f:
                json.dump(claude_config, f, indent=2)
            print(f"✅ Claude Desktop MCP: {claude_config_path}")
    else:
        setup_mcp = False

    # --- Done ---
    print(f"\n{'='*50}")
    print(f"🎉 Mengram ready!\n")
    print(f"   Config:  {config_path}")
    print(f"   Vault:   {vault_path}")
    print(f"   LLM:     {provider}")
    print(f"   Search:  semantic (local embeddings)")

    if setup_mcp:
        print(f"\n   ⚡ Restart Claude Desktop to activate MCP")
        print(f"   Then tell Claude: 'Remember that I work at ...'")
    else:
        print(f"\n   Start MCP server: mengram server")

    print(f"\n   Python SDK:")
    print(f"   >>> from mengram import Memory")
    print(f"   >>> m = Memory(vault_path='{vault_path}', llm_provider='{provider}')")


def cmd_server(args):
    """Start MCP server"""
    local_dir = _local_dir(args)
    if local_dir:
        # Local mode — the folder is the memory; no key, no network.
        if not local_dir.is_dir():
            print(f"❌ No memory folder at {local_dir} — run: mengram local init {local_dir}", file=sys.stderr)
            sys.exit(1)
        import asyncio
        from api.local_mcp_server import main as local_mcp_main
        asyncio.run(local_mcp_main(local_dir))
        return
    if getattr(args, 'cloud', False):
        # Cloud mode — connect to cloud API. Credentials resolve env-first,
        # then ~/.mengram/config.json — same order as hooks (issue #41 class:
        # MCP hosts often spawn without the user's shell profile env).
        api_key = _load_cloud_api_key()
        base_url = _load_cloud_base_url()
        user_id = os.environ.get("MENGRAM_USER_ID", "default")

        if not api_key:
            print("❌ No API key found (checked MENGRAM_API_KEY env and ~/.mengram/config.json)")
            print("   Get one: mengram setup   (or sign up at https://mengram.io)")
            sys.exit(1)

        print(f"🧠 Starting Mengram Cloud MCP server...", file=sys.stderr)
        print(f"   API: {base_url}", file=sys.stderr)

        import asyncio
        from api.cloud_mcp_server import main as cloud_mcp_main
        asyncio.run(cloud_mcp_main())
        return

    config_path = args.config or str(DEFAULT_CONFIG)

    if not Path(config_path).exists():
        print(f"❌ Config not found: {config_path}")
        print(f"   Run: mengram init")
        sys.exit(1)

    print(f"🧠 Starting Mengram MCP server...")
    print(f"   Config: {config_path}")

    # Set working directory to where engine/ is
    try:
        import engine
        engine_dir = Path(engine.__file__).parent.parent
        os.chdir(engine_dir)
    except ImportError:
        pass

    import asyncio
    from api.mcp_server import main as mcp_main
    # Monkey-patch sys.argv for mcp_server
    sys.argv = ["mcp_server", config_path]
    asyncio.run(mcp_main())


def cmd_status(args):
    """Check setup status"""
    print("🧠 Mengram Status\n")

    last = _last_session_receipt(None)
    if last:
        print(last)
        print("   Full receipt: mengram receipt\n")

    # Cloud API key (set by `mengram signup` or `mengram setup`)
    cloud_key = _load_cloud_api_key()
    if cloud_key:
        masked = f"{cloud_key[:10]}...{cloud_key[-4:]}" if len(cloud_key) > 14 else cloud_key
        print(f"✅ Cloud API key: {masked}")
        print(f"   Source: {'env MENGRAM_API_KEY' if os.environ.get('MENGRAM_API_KEY') else _cloud_config_path()}")
        print(f"Configured: yes")
    else:
        print(f"❌ Cloud API key: not set")
        print(f"   Run: mengram signup --email <you@example.com>")
        print(f"Configured: no")
    print()

    # Config
    config_path = DEFAULT_CONFIG
    if config_path.exists():
        print(f"✅ Config: {config_path}")
        with open(config_path) as f:
            config = yaml.safe_load(f)
        print(f"   Provider: {config.get('llm', {}).get('provider', '?')}")
        print(f"   Vault: {config.get('vault_path', '?')}")
    else:
        print(f"ℹ️  No local config (cloud-only install). Run `mengram init` if you want a local vault.")
        return

    # Vault
    vault_path = Path(config.get("vault_path", ""))
    if vault_path.exists():
        notes = list(vault_path.glob("*.md"))
        print(f"✅ Vault: {len(notes)} notes")
    else:
        print(f"⚠️  Vault empty")

    # Vector DB
    vectors_db = vault_path / ".vectors.db"
    if vectors_db.exists():
        size = vectors_db.stat().st_size
        print(f"✅ Vector index: {size / 1024:.0f}KB")
    else:
        print(f"⚠️  No vector index yet (will be created on first use)")

    # Claude Desktop
    claude_config = get_claude_desktop_config_path()
    if claude_config.exists():
        try:
            with open(claude_config) as f:
                cc = json.load(f)
            if "mengram" in cc.get("mcpServers", {}):
                print(f"✅ Claude Desktop MCP configured")
            else:
                print(f"⚠️  Claude Desktop found but MCP not configured")
        except Exception:
            print(f"⚠️  Claude Desktop config error")
    else:
        print(f"⚠️  Claude Desktop not found")

    # sentence-transformers
    try:
        import sentence_transformers
        print(f"✅ sentence-transformers installed")
    except ImportError:
        print(f"⚠️  sentence-transformers not installed: pip install sentence-transformers")


def cmd_receipt(args):
    """What memory did: last session, and the past N days."""
    from local import receipt
    import datetime as _dt
    events = receipt.load()
    if not events:
        print("No receipt yet. The hooks write one line per thing memory does —")
        print("recall on a prompt, a question before a weak workflow, a step recorded.")
        print("Install them: mengram hook install   (or  --memory ./memory)")
        return
    days = getattr(args, "days", 7) or 7
    print("🧠 Mengram receipt\n")
    sid, evs = receipt.previous_session(None, events)
    if evs:
        s = receipt.summarise(evs)
        when = _dt.datetime.fromtimestamp(s["first"]).strftime("%Y-%m-%d %H:%M") if s["first"] else "?"
        print(f"Last session ({when}):")
        print(f"   {receipt.phrase(s)}")
    recent = receipt.since(days, events)
    s = receipt.summarise(recent)
    text = receipt.phrase(s)
    if text:
        print(f"\nPast {days} days, {s['sessions']} session{'s' if s['sessions'] != 1 else ''}:")
        print(f"   {text}")
    else:
        print(f"\nNothing in the past {days} days.")
    print(f"\nLedger: {receipt.path()}")


def cmd_stats(args):
    """Show vault statistics"""
    config_path = args.config or str(DEFAULT_CONFIG)

    if not Path(config_path).exists():
        print(f"❌ Run: mengram init")
        sys.exit(1)

    from engine.brain import create_brain
    # Monkey-patch for config path
    old_argv = sys.argv
    sys.argv = ["", config_path]

    brain = create_brain(config_path)
    stats = brain.get_stats()

    print("🧠 Mengram Stats\n")
    vault = stats.get("vault", {})
    print(f"📁 Notes: {vault.get('total_notes', 0)}")
    for t, count in vault.get("by_type", {}).items():
        print(f"   {t}: {count}")

    if "vectors" in stats:
        v = stats["vectors"]
        print(f"\n🔍 Vector Index: {v.get('total_chunks', 0)} chunks, {v.get('total_entities', 0)} entities")

    sys.argv = old_argv


def cmd_rules(args):
    """Generate CLAUDE.md / .cursorrules from cloud memory"""
    api_key = os.environ.get("MENGRAM_API_KEY", "")
    if not api_key:
        print("❌ Set MENGRAM_API_KEY environment variable", file=sys.stderr)
        sys.exit(1)

    from cloud.client import CloudMemory
    base_url = os.environ.get("MENGRAM_URL", "https://mengram.io")
    mem = CloudMemory(api_key=api_key, base_url=base_url)

    fmt = args.format or "claude_md"
    result = mem.rules(format=fmt, force=args.force)

    if result.get("status") != "ok":
        print(f"❌ {result.get('status', 'unknown')}: {result.get('error', '')}", file=sys.stderr)
        sys.exit(1)

    print(result["content"])


def get_claude_code_settings_path() -> Path:
    """Path to Claude Code user settings"""
    return Path.home() / ".claude" / "settings.json"


def output_hook_success():
    """Output success JSON for Claude Code hook and exit cleanly."""
    print(json.dumps({"continue": True, "suppressOutput": True}))
    sys.exit(0)


def _is_quota_error(e: Exception) -> bool:
    """Check if exception is a QuotaExceededError (without importing the class)."""
    return type(e).__name__ == "QuotaExceededError"


def _local_dir(args):
    """The memory folder when local mode is on (--memory or MENGRAM_MEMORY_DIR)."""
    from local.config import memory_dir
    return memory_dir(getattr(args, "memory", None))


def _local_store(local_dir):
    from local.store import LocalStore
    return LocalStore(local_dir)


def _local_auto_recall(args, EVENT, HOOK, local_dir, prompt, session_id=None):
    context = _local_store(local_dir).recall(prompt, limit=3)
    if not context:
        _emit_hook_exit(EVENT, args, HOOK, "no memories found (local)")
    _receipt("recall", session_id)
    _emit_hook_exit(EVENT, args, HOOK, "found memories (local)", context=context)


def _local_auto_context(args, EVENT, HOOK, local_dir, system_message=None, prefix=None):
    profile = _local_store(local_dir).profile()
    if not profile:
        _emit_hook_exit(EVENT, args, HOOK, "empty folder (local)",
                        context=prefix, system_message=system_message)
    context = f"[Mengram Memory — context loaded from {local_dir}]\n{profile}"
    if prefix:
        context = prefix + "\n\n" + context
    _emit_hook_exit(EVENT, args, HOOK, f"context loaded ({len(profile)} chars, local)",
                    context=context, system_message=system_message)


def _local_auto_save(args, EVENT, HOOK, local_dir, messages, session_id=None):
    from local.config import llm_client
    client = llm_client(local_dir)
    if client is None:
        _emit_hook_exit(EVENT, args, HOOK, "no model configured (local) — nothing saved")
    _local_store(local_dir).add(messages, client)
    _receipt("save", session_id)
    _emit_hook_exit(EVENT, args, HOOK, "saved (local)")


def _receipt(kind, session_id, **fields):
    """Leave a line in the receipt ledger. Never raises, never prints."""
    try:
        from local import receipt
        receipt.note(kind, session_id, **fields)
    except Exception:
        pass


def _read_hook_input():
    """The JSON a Claude Code hook receives on stdin; {} when there is none.

    A terminal is not a hook: reading from one would block a manual run."""
    try:
        if sys.stdin.isatty():
            return {}
        return json.loads(sys.stdin.read() or "{}") or {}
    except Exception:
        return {}


def _local_import_claude_code(args, local_dir) -> int:
    """`mengram import claude-code --memory DIR`: the cold start for a folder.
    Each session is one extraction with the folder's own model; the folder
    keeps its own imported-sessions list, separate from the cloud account's."""
    from importer import import_claude_code, discover_claude_code_sessions
    from local.config import describe_model, llm_client

    if not local_dir.is_dir():
        print(f"❌ No memory folder at {local_dir} — run: mengram local init {local_dir}")
        return 1
    try:
        client = llm_client(local_dir)
    except ImportError as e:
        print(f"❌ {e}")
        return 2
    if client is None:
        print("❌ No model configured — extraction needs one.\n"
              f"   mengram local init {local_dir} --provider anthropic --api-key sk-ant-...   (or openai / ollama)\n"
              "   or export ANTHROPIC_API_KEY / OPENAI_API_KEY")
        return 2

    available = discover_claude_code_sessions(getattr(args, "project", "") or "")
    if not available:
        print("❌ No Claude Code sessions found in ~/.claude/projects/")
        return 1
    n = min(getattr(args, "last", 20), len(available))
    print(f"🧠 Found {len(available)} Claude Code sessions; importing up to {n} most recent into {local_dir}.")
    print(f"   Each session = 1 extraction with {describe_model(local_dir)}. Nothing leaves your machine")
    print("   except the calls to that model.")
    if not getattr(args, "yes", False):
        answer = input("   Continue? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Aborted.")
            return 0

    store = _local_store(local_dir)
    totals = {"entities_created": 0, "entities_updated": 0, "facts_added": 0, "episodes_saved": 0,
              "procedures_created": 0, "procedures_refreshed": 0}

    def add_fn(text, session_id):
        stats = store.add(text, client)
        for key in ("entities_created", "entities_updated", "facts_added", "episodes_saved"):
            totals[key] += int(stats.get(key) or 0)
        procs = stats.get("procedures") or {}
        totals["procedures_created"] += int(procs.get("created") or 0)
        totals["procedures_refreshed"] += int(procs.get("refreshed") or 0)
        return {}

    def progress(current, total, title):
        pct = int(current / total * 100) if total else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r  {bar} {pct}% ({current}/{total}) {title}", end="", flush=True)

    print()
    result = import_claude_code(
        add_fn,
        last=getattr(args, "last", 20),
        project_filter=getattr(args, "project", "") or "",
        reimport=getattr(args, "reimport", False),
        on_progress=progress,
        state_file=local_dir / ".mengram" / "claude-code-imported.json",
    )

    print(f"\n\n{'='*50}")
    print("✅ Import complete!\n")
    print(f"   Sessions considered: {result.conversations_found}")
    print(f"   Imported:            {result.chunks_sent}")
    print(f"   Time:                {result.duration_seconds:.1f}s")
    if result.errors:
        print(f"\n   ⚠️  {len(result.errors)} errors:")
        for err in result.errors[:5]:
            print(f"      - {err}")

    if result.chunks_sent > 0:
        s = store.stats()
        print(f"\n   🧠 Folder now holds: {s['entities']} entities, {s['facts']} facts, "
              f"{s['episodes']} episodes, {s['procedures']} workflows")
        print(f"   This run: entities +{totals['entities_created']} (updated {totals['entities_updated']}), "
              f"facts +{totals['facts_added']}, episodes +{totals['episodes_saved']}, "
              f"workflows +{totals['procedures_created']} (refreshed {totals['procedures_refreshed']})")
        procs = store.procedures(limit=3)
        if procs:
            print("\n   Learned workflows (these evolve as you succeed or fail):")
            for p in procs:
                print(f"      ⚙ {p.get('name', '?')} — {len(p.get('steps') or [])} steps, {p.get('reliability') or 'untested'}")
        # The answer to "what do you know about me?" — one page, nothing uploaded.
        try:
            from local.evolve import read_quarantine
            from local.map import write_map
            map_file = write_map(store, read_quarantine(store), model=describe_model(local_dir))
            print(f"\n   🗺  Map of the folder: {map_file}   (open it in a browser; mengram local map --open)")
        except Exception as e:  # the import succeeded; the map is a bonus
            print(f"\n   (map not written: {e})")
    elif result.conversations_found == 0:
        print("\n   Nothing new — every session is already in the folder (use --reimport to force).")

    print(f"\n   Try: mengram local search \"deploy\" --memory {local_dir}")
    print(f"   Hooks: mengram hook install --memory {local_dir}")
    print("   Already-imported sessions are skipped on re-runs (use --reimport to force).")
    return 0


def cmd_auto_recall(args):
    """Hook handler — called by Claude Code on UserPromptSubmit. Searches Mengram for relevant context."""
    HOOK = "auto-recall"
    EVENT = "UserPromptSubmit"
    try:
        local_dir = _local_dir(args)
        api_key = None if local_dir else _load_cloud_api_key()
        if not local_dir and not api_key:
            _emit_hook_exit(EVENT, args, HOOK, "no API key")

        # Read hook input from stdin
        try:
            input_data = json.loads(sys.stdin.read())
        except Exception:
            _emit_hook_exit(EVENT, args, HOOK, "no input")

        prompt = input_data.get("prompt", "")
        if not prompt or len(prompt) < 10:
            _emit_hook_exit(EVENT, args, HOOK, "skipped (short/command prompt)")

        # Skip common non-question prompts
        skip_prefixes = ["/", "yes", "no", "ok", "y", "n", "da", "нет", "да"]
        prompt_lower = prompt.strip().lower()
        if any(prompt_lower == p or prompt_lower.startswith(p + " ") for p in skip_prefixes):
            _emit_hook_exit(EVENT, args, HOOK, "skipped (short/command prompt)")

        session_id = input_data.get("session_id")
        if local_dir:
            _local_auto_recall(args, EVENT, HOOK, local_dir, prompt, session_id)

        from cloud.client import CloudMemory
        base_url = _load_cloud_base_url()
        user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")

        # Marked as the user's own automation: not charged to the search quota.
        mem = CloudMemory(api_key=api_key, base_url=base_url, source="hook",
                          host=_hook_host(args, input_data))
        results = mem.search(prompt, user_id=user_id, limit=3, graph_depth=1)

        if not results:
            _emit_hook_exit(EVENT, args, HOOK, "no memories found")

        # Vector search has no way to say "nothing here is close enough", so it
        # answers every prompt with its three nearest entities however far away
        # they are. Folder mode is silent when no word matches; this gives the
        # cloud path the same right to say nothing. Set
        # MENGRAM_RECALL_LEXICAL_GUARD=0 to keep the raw results.
        if os.environ.get("MENGRAM_RECALL_LEXICAL_GUARD", "1") != "0":
            from cloud.relevance import filter_results
            kept = filter_results(prompt, results)
            if not kept:
                _emit_hook_exit(EVENT, args, HOOK,
                                f"{len(results)} found, none related to the prompt")
            results = kept

        # Format context
        lines = ["[Mengram Memory — relevant context from past sessions]"]
        for r in results:
            entity = r.get("entity", "")
            facts = r.get("facts", [])
            if entity and facts:
                lines.append(f"\n{entity}:")
                metas = r.get("facts_meta") or []
                for k, fact in enumerate(facts[:5]):
                    lines.append(_fact_line(fact, metas[k] if k < len(metas) else None))

        context = "\n".join(lines)
        _receipt("recall", session_id)
        _emit_hook_exit(EVENT, args, HOOK, f"found {len(results)} memories", context=context)

    except SystemExit:
        raise
    except Exception as e:
        if _is_quota_error(e):
            _emit_hook_exit(
                EVENT, args, HOOK, "quota exceeded",
                context=(
                    "[Mengram] Memory search quota exceeded — recall is disabled. "
                    f"{e} "
                    "Upgrade at https://mengram.io/dashboard"
                ),
            )
        _emit_hook_exit(EVENT, args, HOOK, "error")


def _weekly_state_path():
    return Path.home() / ".mengram" / "weekly-shown.json"


def _last_session_receipt(current_session_id):
    """One line on what memory did last session, or None. Never raises."""
    try:
        from local import receipt
        return receipt.session_line(current_session_id)
    except Exception:
        return None


def _iso_week_now():
    import datetime as _dt
    y, w, _ = _dt.date.today().isocalendar()
    return f"{y}-W{w:02d}"


def _maybe_weekly_message(mem, user_id):
    """Once per ISO week, on the first SessionStart, return a compact plain-text
    weekly report to show the user via systemMessage. Best-effort: any failure
    (quota, network, no data) silently returns None. Never nags an empty week."""
    try:
        week = _iso_week_now()
        state_path = _weekly_state_path()
        shown = {}
        if state_path.exists():
            try:
                shown = json.loads(state_path.read_text())
            except Exception:
                shown = {}
        key = user_id or "default"
        if shown.get(key) == week:
            return None  # already shown this week

        stats = mem.weekly_stats(user_id=user_id)
        # Mark shown regardless (so we don't retry every session all week)
        shown[key] = week
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(json.dumps(shown))
        except Exception:
            pass

        facts = stats.get("facts_learned", 0)
        procs = stats.get("procedures_learned", 0)
        recalls = stats.get("recalls_served", 0)
        prevented = stats.get("prevented", [])
        if not (procs or recalls or prevented) and facts < 5:
            return None  # an empty or barely-started week — don't nag

        lines = ["🧠 Mengram — your AI's memory, past 7 days:"]
        lines.append(f"   {facts} facts learned · {procs} procedures · {recalls} recalls served")
        if prevented:
            lines.append(f"   Repeated mistakes prevented: {len(prevented)}")
            for p in prevented[:3]:
                bitten = p.get("last_bitten")
                tail = f" (last bitten {bitten})" if bitten else ""
                lines.append(f"     · {(p.get('name') or '?')[:40]}{tail}")
        lines.append("   Full report: run  mengram weekly --share")
        return "\n".join(lines)
    except Exception:
        return None


def _restored_state(input_data) -> tuple[str | None, str | None]:
    """The working state written before compaction, read back once:
    `(context for the model, one line for the person)`. `(None, None)` when
    there is nothing on file for this session or directory."""
    try:
        from local import checkpoint
        snap = checkpoint.consume(input_data.get("session_id"), input_data.get("cwd"))
        if snap is None or checkpoint.is_empty(snap):
            return None, None
        _receipt("restore", input_data.get("session_id"),
                 files=len(snap.get("files") or []), host=snap.get("host"))
        return checkpoint.render(snap), checkpoint.headline(snap)
    except Exception:
        return None, None


def cmd_auto_checkpoint(args):
    """Hook handler — PreCompact. Writes the working state down before the
    host summarises it away; `auto-context` reads it back after."""
    HOOK = "auto-checkpoint"
    EVENT = "PreCompact"
    try:
        input_data = _read_hook_input()
        if getattr(args, "host", None) == "cursor":
            input_data = _cursor_input(input_data)
        transcript = input_data.get("transcript_path")
        session_id = input_data.get("session_id")
        if not transcript or not os.path.isfile(transcript):
            _emit_hook_exit(EVENT, args, HOOK, "no transcript")

        from local import checkpoint
        snap = checkpoint.snapshot(transcript, session_id, cwd=input_data.get("cwd"),
                                   trigger=input_data.get("trigger"),
                                   host=getattr(args, "host", None))
        if checkpoint.is_empty(snap):
            _emit_hook_exit(EVENT, args, HOOK, "nothing to keep")
        if checkpoint.save(snap) is None:
            _emit_hook_exit(EVENT, args, HOOK, "could not write checkpoint")
        _receipt("checkpoint", session_id, files=len(snap["files"]),
                 trigger=snap.get("trigger"), host=snap.get("host"))
        _emit_hook_exit(EVENT, args, HOOK,
                        f"working state saved ({len(snap['files'])} files, "
                        f"{len(snap['prompts'])} prompts)")
    except SystemExit:
        raise
    except Exception:
        _emit_hook_exit(EVENT, args, HOOK, "error")


def cmd_auto_restore(args):
    """Hook handler — Cursor `postToolUse`. Cursor opens no new session after a
    compaction, so the checkpoint written at `preCompact` comes back on the
    first tool call after it, as `additional_context`. Says nothing when there
    is nothing on file for this conversation; costs one file check."""
    HOOK = "auto-restore"
    EVENT = "PostToolUse"
    try:
        input_data = _cursor_input(_read_hook_input())
        from local import checkpoint
        snap = checkpoint.load(input_data.get("session_id"))
        if snap is None or checkpoint.is_empty(snap):
            _emit_hook_exit(EVENT, args, HOOK, "nothing to restore")
        try:
            checkpoint.path_for(snap.get("session") or "unknown").unlink()
        except OSError:
            pass
        _receipt("restore", input_data.get("session_id"),
                 files=len(snap.get("files") or []), host="cursor")
        _emit_hook_exit(EVENT, args, HOOK, "working state restored",
                        context=checkpoint.headline(snap) + "\n\n" + checkpoint.render(snap))
    except SystemExit:
        raise
    except Exception:
        _emit_hook_exit(EVENT, args, HOOK, "error")


def _cursor_auto_context(args, input_data):
    """Cursor `sessionStart`: a new conversation, not a resumed one. The profile
    goes in, and the newest working state saved in this workspace within the
    last day — the person who opens a new chat ten minutes after compaction
    ate the old one is continuing, not starting over."""
    HOOK = "auto-context"
    EVENT = "SessionStart"
    parts = []
    try:
        from local import checkpoint
        snap = checkpoint.consume(input_data.get("session_id"), input_data.get("cwd"), max_age=86400)
        if snap is not None and not checkpoint.is_empty(snap):
            _receipt("restore", input_data.get("session_id"),
                     files=len(snap.get("files") or []), host="cursor")
            parts.append(checkpoint.headline(snap) + "\n\n" + checkpoint.render(snap))
    except Exception:
        pass
    card_block, card_line = _resume_block(input_data)
    if card_block:
        parts.insert(0, card_block if card_block == card_line else card_line + "\n\n" + card_block)
    try:
        local_dir = _local_dir(args)
        if local_dir:
            profile = _local_store(local_dir).profile()
            if profile:
                parts.append(f"[Mengram Memory — context loaded from {local_dir}]\n{profile}")
        else:
            api_key = _load_cloud_api_key()
            if api_key:
                from cloud.client import CloudMemory
                user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")
                mem = CloudMemory(api_key=api_key, base_url=_load_cloud_base_url())
                system_prompt = (mem.get_profile(user_id=user_id) or {}).get("system_prompt", "")
                if system_prompt:
                    parts.append(f"[Mengram Memory — user context loaded from past sessions]\n{system_prompt}")
    except Exception:
        pass
    _emit_hook_exit(EVENT, args, HOOK, "context loaded" if parts else "nothing to load",
                    context="\n\n".join(parts) if parts else None)


def _write_resume_card(args, input_data, api_key=None):
    """At Stop: record where the task stands (local/resume.py). Deterministic
    parts always; the model draft only with a key and only when the record
    changed. Never raises, never delays the hook noticeably."""
    from local import resume
    transcript = input_data.get("transcript_path")
    cwd = input_data.get("cwd") or os.getcwd()
    session_id = input_data.get("session_id", "unknown")
    key = resume.card_key(resume.repo_info(cwd))
    card = resume.build(transcript, session_id, cwd, host=_hook_tool(args, input_data),
                        previous=resume.load(key))
    if resume.is_empty(card):
        return None
    if api_key and resume.needs_draft(card):
        try:
            from cloud.client import CloudMemory
            mem = CloudMemory(api_key=api_key, base_url=_load_cloud_base_url(), source="hook",
                              host=_hook_host(args, input_data))
            resume.apply_draft(card, mem.draft_resume(resume.draft_payload(card)))
        except Exception:
            pass
    path = resume.save(card)
    if path:
        _receipt("resume", session_id, task=bool(card.get("task")), remaining=len(card.get("remaining") or []))
    return card


def _resume_block(input_data) -> tuple[str | None, str | None]:
    """The task card a starting session should see, and the line for the person.

    Only this branch's card goes into context. In Orca every agent gets its own
    worktree and branch, so another branch's card is usually another agent's
    task; the session is told the cards exist and loads one on request."""
    try:
        from local import resume
        cwd = input_data.get("cwd") or os.getcwd()
        card, relation = resume.load_for(cwd)
        if card is None or resume.is_empty(card):
            return None, None
        if relation != "exact":
            note = resume.other_branch_note(card)
            return note, note
        resume.mark_read(card, input_data.get("session_id"))
        return resume.render(card, cwd, relation), resume.headline(card, relation)
    except Exception:
        return None, None


def cmd_resume(args):
    """`mengram resume`: where the task in this repository stands."""
    from local import resume
    cwd = getattr(args, "path", None) or os.getcwd()
    if getattr(args, "open", False):
        from local import resume_page
        print("Serving the resume page on localhost (Ctrl-C to stop)…")
        resume_page.serve(cwd)
        return
    card, relation = resume.load_for(cwd, max_age=10 ** 9 if getattr(args, "any_age", False) else resume.MAX_AGE)
    if card is None:
        info = resume.repo_info(cwd)
        print(f"No task card for {info.get('remote') or info.get('root')} yet. One is written when an agent "
              f"with the Mengram hooks stops working here.")
        return
    if getattr(args, "json", False):
        print(json.dumps(card, ensure_ascii=False, indent=1))
        return
    print(resume.render(card, cwd, relation))
    if card.get("draft") and (card.get("task") or card.get("done")):
        print("\nTask/done/remaining are the agent's draft. `mengram resume --open` to confirm or correct them.")


def _hook_tool(args, input_data=None) -> str:
    """Which tool this hook is running under: claude-code, codex or cursor."""
    host = getattr(args, "host", None)
    if host in ("cursor", "codex", "claude-code"):
        return host
    d = input_data or {}
    if "cursor_version" in d or "conversation_id" in d and "generation_id" in d:
        return "cursor"
    # The transcript path names the host before the environment does: Orca
    # exports CODEX_HOME into every terminal it opens, Claude Code included.
    transcript = str(d.get("transcript_path", "")).replace("\\", "/").lower()
    if "/.claude/" in transcript:
        return "claude-code"
    if "codex" in transcript:
        return "codex"
    if os.environ.get("CLAUDECODE") == "1":
        return "claude-code"
    if os.environ.get("CODEX_HOME"):
        return "codex"
    return "claude-code"


def _hook_os() -> str:
    return {"darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux" if sys.platform.startswith("linux") else sys.platform)


def _hook_host(args, input_data=None) -> str:
    """The X-Mengram-Host value: "<os>/<tool>" (cloud/provenance.py)."""
    return f"{_hook_os()}/{_hook_tool(args, input_data)}"


def _hook_provenance(args, input_data, session_id) -> dict:
    """What a hook records on every fact it saves: tool, session, cwd, os."""
    return {"source": _hook_tool(args, input_data),
            "metadata": {"session_id": session_id, "cwd": input_data.get("cwd") or os.getcwd(),
                         "os": _hook_os(), "tool": _hook_tool(args, input_data)}}


def _fact_line(fact, meta=None) -> str:
    """A recalled fact with its provenance tag: "  - fact  (claude-code, 2026-09-15)"."""
    from cloud.provenance import tag
    t = tag(meta)
    return f"  - {fact}  ({t})" if t else f"  - {fact}"


def cmd_auto_context(args):
    """Hook handler — called by Claude Code on SessionStart. Loads cognitive profile as context."""
    HOOK = "auto-context"
    EVENT = "SessionStart"
    restored = None
    restored_line = None
    try:
        input_data = _read_hook_input()
        if getattr(args, "host", None) == "cursor":
            _cursor_auto_context(args, _cursor_input(input_data))
        source = input_data.get("source", "startup")
        # What memory did last session, said once, at the one moment the
        # person is deciding whether it is worth keeping. A resumed or
        # compacted session is the same session, so nothing is said there.
        receipt_msg = None
        if source in ("startup", "clear"):
            receipt_msg = _last_session_receipt(input_data.get("session_id"))

        # After a compaction or a resume, the work itself comes first: the
        # person's last prompt and the files just edited matter more right
        # now than their tech stack, and they are what the host's summary
        # loses. Restored whether or not there is an account: the checkpoint
        # was written on this machine and never left it.
        if source in ("compact", "resume"):
            restored, restored_line = _restored_state(input_data)
            # Said to the person, not only to the model: the restore is the one
            # moment memory visibly does something, and silent it is the moment
            # nobody can point at.
            if restored_line:
                receipt_msg = restored_line if not receipt_msg else receipt_msg + "\n\n" + restored_line

        # A new session in a repository with a task card: where the task stands
        # comes first (local/resume.py). Not after compaction — that session is
        # still the one that wrote the card.
        if source in ("startup", "clear", "resume"):
            card_block, card_line = _resume_block(input_data)
            if card_block:
                restored = card_block if not restored else card_block + "\n\n" + restored
                receipt_msg = card_line if not receipt_msg else receipt_msg + "\n\n" + card_line

        local_dir = _local_dir(args)
        if local_dir:
            _local_auto_context(args, EVENT, HOOK, local_dir,
                                system_message=receipt_msg, prefix=restored)
        api_key = _load_cloud_api_key()
        if not api_key:
            _emit_hook_exit(EVENT, args, HOOK,
                            "working state restored (no API key)" if restored else "no API key",
                            context=restored, system_message=receipt_msg)

        from cloud.client import CloudMemory
        base_url = _load_cloud_base_url()
        user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")

        mem = CloudMemory(api_key=api_key, base_url=base_url)
        profile = mem.get_profile(user_id=user_id)

        weekly_msg = None if getattr(args, "no_weekly", False) else _maybe_weekly_message(mem, user_id)
        shown = "\n\n".join(m for m in (receipt_msg, weekly_msg) if m) or None

        system_prompt = profile.get("system_prompt", "")
        if not system_prompt:
            _emit_hook_exit(EVENT, args, HOOK, "no profile", context=restored, system_message=shown)

        context = f"[Mengram Memory — user context loaded from past sessions]\n{system_prompt}"
        if restored:
            context = restored + "\n\n" + context
        _emit_hook_exit(EVENT, args, HOOK,
                        f"context loaded ({len(system_prompt)} chars"
                        f"{', working state restored' if restored else ''})",
                        context=context, system_message=shown)

    except SystemExit:
        raise
    except Exception as e:
        if _is_quota_error(e):
            _emit_hook_exit(
                EVENT, args, HOOK, "quota exceeded",
                context=(
                    (restored + "\n\n" if restored else "")
                    + f"[Mengram] Memory profile load failed — quota exceeded. {e} "
                    "Upgrade at https://mengram.io/dashboard"
                ),
            )
        # A cloud outage must not cost the work that was saved locally.
        _emit_hook_exit(EVENT, args, HOOK, "error", context=restored, system_message=restored_line)


def cmd_auto_save(args):
    """Hook handler — called by Claude Code on Stop event. Reads stdin, saves to Mengram."""
    HOOK = "auto-save"
    EVENT = "Stop"
    try:
        local_dir = _local_dir(args)
        api_key = None if local_dir else _load_cloud_api_key()

        # Read hook input from stdin
        try:
            input_data = json.loads(sys.stdin.read())
        except Exception:
            _emit_hook_exit(EVENT, args, HOOK, "no input")
        if getattr(args, "host", None) == "cursor":
            # afterAgentResponse: the final text arrives as `text`; the transcript
            # format is undocumented, so the user's side is left out rather than
            # guessed at (see local/checkpoint.py for the tolerant reader).
            input_data = _cursor_input(input_data)
            input_data.pop("transcript_path", None)

        # Avoid infinite loops
        if input_data.get("stop_hook_active"):
            _emit_hook_exit(EVENT, args, HOOK, "skipped (stop_hook_active)")

        # Where the task stands, for whoever continues it (local/resume.py).
        # Local, before any account check: the card must exist without one.
        try:
            _write_resume_card(args, input_data, api_key=api_key)
        except Exception:
            pass

        if not local_dir and not api_key:
            _emit_hook_exit(EVENT, args, HOOK, "no API key")

        last_msg = input_data.get("last_assistant_message", "")
        if not last_msg or len(last_msg.strip()) < 10:
            _emit_hook_exit(EVENT, args, HOOK, "skipped (short response)")

        # Throttle: only save every Nth response
        session_id = input_data.get("session_id", "unknown")
        every = getattr(args, "every", 3) or 3
        import tempfile
        counter_file = Path(tempfile.gettempdir()) / f"mengram-hook-{session_id}.count"

        count = 0
        try:
            if counter_file.exists():
                count = int(counter_file.read_text().strip())
        except Exception:
            count = 0

        count += 1
        try:
            counter_file.write_text(str(count))
        except Exception:
            pass

        if count > 1 and count % every != 0:
            _emit_hook_exit(EVENT, args, HOOK, f"throttled ({count}/{every})")

        # Extract last user message from transcript
        user_message = ""
        transcript_path = input_data.get("transcript_path", "")
        if transcript_path and _hook_tool(args, input_data) == "codex":
            # A Codex rollout: the shared reader skips its environment and
            # shell-command wrappers, which are not the person's words.
            try:
                from local import checkpoint
                prompts = checkpoint.snapshot(transcript_path, session_id).get("prompts") or []
                user_message = prompts[-1] if prompts else ""
            except Exception:
                user_message = ""
        elif transcript_path and Path(transcript_path).exists():
            try:
                with open(transcript_path, "r") as f:
                    lines = f.readlines()
                # Read last 500 lines max for performance
                for line in reversed(lines[-500:]):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") == "user":
                            content = entry.get("message", {}).get("content", "")
                            if isinstance(content, list):
                                parts = []
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        parts.append(item.get("text", ""))
                                    elif isinstance(item, str):
                                        parts.append(item)
                                user_message = " ".join(parts)
                            elif isinstance(content, str):
                                user_message = content
                            break
                    except Exception:
                        continue
            except Exception:
                pass

        # Skip interrupted requests
        if user_message.startswith("[Request interrupted"):
            user_message = ""

        # Build messages
        messages = []
        if user_message:
            messages.append({"role": "user", "content": user_message})
        messages.append({"role": "assistant", "content": last_msg})

        if local_dir:
            _local_auto_save(args, EVENT, HOOK, local_dir, messages, session_id)

        # Send to Mengram API
        from cloud.client import CloudMemory
        base_url = _load_cloud_base_url()
        user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")

        mem = CloudMemory(api_key=api_key, base_url=base_url)
        mem.add(
            messages,
            user_id=user_id,
            app_id="claude-code",
            agent_id="auto-save",
            run_id=session_id,
            **_hook_provenance(args, input_data, session_id),
        )

        _receipt("save", session_id)
        _emit_hook_exit(EVENT, args, HOOK, "saved")

    except SystemExit:
        raise
    except Exception as e:
        if _is_quota_error(e):
            # Always surface quota errors via stderr (Stop hooks don't support
            # additionalContext), regardless of --verbose.
            print(
                f"\n⚠️  [Mengram] Memory save failed — quota exceeded. {e}\n"
                "   Your conversations are NOT being saved.\n"
                "   Upgrade at https://mengram.io/dashboard\n",
                file=sys.stderr,
            )
            _emit_hook_exit(EVENT, args, HOOK, "quota exceeded")
        _emit_hook_exit(EVENT, args, HOOK, "error")


def cmd_auto_policy(args):
    """Hook handler — Claude Code PreToolUse on Bash.

    If the command matches a learned workflow whose record is weak, answer
    "ask" so the human confirms, and hand Claude the plan on record. Every
    other path prints nothing and exits 0: a hook that fails must never
    stop a command that would otherwise have run.
    """
    HOOK = "auto-policy"
    from cloud import policy

    def _exit(status, verdict=None):
        marker = _hook_marker(HOOK, status) if getattr(args, "verbose", False) else None
        out = policy.hook_output(verdict, marker)
        if out is not None:
            print(json.dumps(out))
        sys.exit(0)

    try:
        try:
            input_data = json.loads(sys.stdin.read())
        except Exception:
            _exit("no input")
        if input_data.get("tool_name") != "Bash":
            _exit("skipped (not Bash)")
        command = (input_data.get("tool_input") or {}).get("command") or ""

        # Local memfmt folder first: no account, no network, no quota. Loaded
        # before the workflow filter because the intent is noted for every
        # command that is a step, gate or no gate: the outcome half needs the
        # judgement written down for exactly the commands it will later record.
        local_root = _local_dir(args)
        procs = None
        if local_root:
            procs = policy.memfmt_procedures(str(local_root))
            _note_intent(input_data, local_root, procs, command)

        if not policy.looks_like_workflow(command, os.environ.get("MENGRAM_POLICY_PATTERN")):
            _exit("skipped (not a workflow command)")

        min_reliable = getattr(args, "min_reliable", None)
        if min_reliable is None:
            try:
                min_reliable = int(os.environ.get("MENGRAM_POLICY_MIN_RELIABLE", policy.DEFAULT_MIN_RELIABLE))
            except ValueError:
                min_reliable = policy.DEFAULT_MIN_RELIABLE

        if not local_root:
            api_key = _load_cloud_api_key()
            if not api_key:
                _exit("no API key")
            from cloud.client import CloudMemory
            user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")
            mem = CloudMemory(api_key=api_key, base_url=_load_cloud_base_url())
            procs = mem.procedures(query=command[:300], limit=3, user_id=user_id)

        # The same bar the recorder uses: the command has to *be* a step of
        # this workflow, not merely share a word with it. Sharing one word was
        # enough to turn one command in six into a confirmation prompt, which
        # is the version of this gate nobody would keep installed.
        hit = policy.best_step_match(procs, command)
        if hit is None:
            _exit("no matching workflow step")
        proc = hit[0]
        verdict = policy.decide(proc, command, min_reliable=min_reliable)
        if verdict is None:
            _exit(f"'{proc.get('name')}' {policy.reliability_of(proc)} — allowed")
        _receipt("gate", input_data.get("session_id"),
                 procedure=verdict["name"], reliability=verdict["reliability"])
        _exit(f"'{verdict['name']}' {verdict['reliability']} — ask", verdict)

    except SystemExit:
        raise
    except Exception:
        _exit("error")


def _note_intent(input_data, local_root, procs, command) -> None:
    """PreToolUse: write down which step this command is, before it runs.

    Keyed by the id the host gives the call, so the outcome half can close
    exactly this record instead of matching the command text a second time.
    Same net as the recorder: the command has to name a known tool and be a
    step of a workflow on record. Never raises — it is bookkeeping inside a
    hook that must not stop a command.
    """
    try:
        from cloud import policy
        from local import intents
        tool_use_id = input_data.get("tool_use_id")
        if not tool_use_id or not command or not policy.shell_verbs(command):
            return
        hit = policy.best_step_match(procs, command)
        if hit is None:
            return
        proc, step_no, _score = hit
        transcript_path = input_data.get("transcript_path")
        try:
            offset = os.path.getsize(transcript_path) if transcript_path else 0
        except OSError:
            offset = 0
        intents.open_intent(local_root, tool_use_id=tool_use_id,
                            procedure=proc.get("name") or "unnamed workflow",
                            step=step_no, command=command,
                            session_id=input_data.get("session_id"),
                            transcript_path=transcript_path, offset=offset)
    except Exception:
        return


#: An intent with no result in its transcript after this long is a command
#: that never finished where anyone could see it; it is dropped, not charged.
_INTENT_UNRESOLVED_SECONDS = 24 * 3600


def _settle_open_intents(local_root, store, current_tool_use_id, seen: list) -> str:
    """Resolve earlier intents against their transcripts, by id.

    A command that exited non-zero never sends a PostToolUse, so its intent is
    still open when the next event comes round; the transcript holds its
    `Exit code N` and that is charged to the step it was noted as. A declined
    or refused command is closed and charged nothing: it never ran. A result
    that reads as a success is closed without recording — its own PostToolUse
    may still be on its way, and one success must not be counted twice.

    Adds every charged id to `seen`, so the transcript sweep that follows does
    not charge it again. Returns a fragment for the verbose marker.
    """
    from local import intents
    from local import transcript as tr
    import time
    charged = 0
    for intent in intents.open_intents(local_root):
        tid = intent.get("tool_use_id")
        if not tid or tid == current_tool_use_id:
            continue
        transcript_path = intent.get("transcript_path")
        result = (tr.result_for(transcript_path, tid, intent.get("offset") or 0)
                  if transcript_path else None)
        if result is None:
            age = time.time() - float(intent.get("opened_at") or 0)
            if age > _INTENT_UNRESOLVED_SECONDS:
                intents.close_intent(local_root, tid)
            continue
        kind, body = result
        intents.close_intent(local_root, tid)
        if kind == "failed":
            store.step_outcome(intent.get("procedure") or "unnamed workflow",
                               int(intent.get("step") or 0),
                               success=False, reason=tr._reason(body))
            _receipt("step", intent.get("session_id"), procedure=intent.get("procedure"),
                     step=int(intent.get("step") or 0), ok=False, source="transcript")
            seen.append(tid)
            charged += 1
    return f"; {charged} failure(s) from open intents" if charged else ""


def _bash_outcome(tool_response) -> bool | None:
    """Did this command work? None when the event does not say.

    Measured against a real payload rather than the documentation, which
    describes an `exit_code` this host does not send. What actually arrives for
    Bash is `stdout`, `stderr`, `interrupted`, `isImage`, `noOutputExpected` —
    and, decisively, the event arrives *only when the command succeeded*. A
    command that exits non-zero fires no PostToolUse at all (verified: a
    logging hook recorded the successful marker command and never the failing
    one). So the arrival of the event is itself the signal.

    That makes failures unobservable from here. They are not guessed at: this
    returns True or None, never False, unless a host does send an exit code.
    A non-empty stderr is deliberately not a failure — plenty of healthy tools
    write there — and an interrupted command is not evidence of anything.
    """
    if not isinstance(tool_response, dict):
        return None
    if tool_response.get("interrupted"):
        return None
    # A command that ran out of time did not finish, and this host moves it to
    # the background rather than killing it, so it may yet succeed. Measured
    # with a logging hook: a timed-out call *does* fire this event, carrying
    # `timedOutAfterMs` and `backgroundTaskId` and `interrupted: false`. Without
    # this check the rule below reads that as a success, which is the same
    # survivorship bias as the missing-failure event, wearing a different hat.
    if tool_response.get("timedOutAfterMs") is not None:
        return None
    if tool_response.get("backgroundTaskId"):
        return None
    code = tool_response.get("exit_code", tool_response.get("exitCode"))
    if isinstance(code, bool):          # True is not an exit code
        return None
    if isinstance(code, int):
        return code == 0
    flag = tool_response.get("is_error", tool_response.get("isError"))
    if isinstance(flag, bool):
        return not flag
    # No verdict in the payload, and this event only fires after a success.
    return True


def _failure_reason(tool_response) -> str | None:
    """The shortest honest description of what went wrong."""
    if not isinstance(tool_response, dict):
        return None
    text = (tool_response.get("stderr") or "").strip()
    if not text:
        text = (tool_response.get("stdout") or "").strip()
    if not text:
        return None
    line = text.splitlines()[-1].strip()
    return line[:200] or None


def _record_transcript_failures(transcript_path, local_root, procs, store, session_id=None) -> str:
    """Charge the steps whose commands failed, from the session transcript.

    Returns a fragment for the verbose marker, empty when nothing was found —
    which is the usual case, and the quiet one.
    """
    if not transcript_path:
        return ""
    from cloud import policy
    from local import transcript as tr
    try:
        cursor = tr.read_cursor(local_root)
        failures, cursor = tr.new_failures(transcript_path, cursor)
        recorded = 0
        for command, reason in failures:
            hit = policy.best_step_match(procs, command)
            if hit is None:
                continue
            proc, step_no, _score = hit
            store.step_outcome(proc.get("name") or "unnamed workflow", step_no,
                               success=False, reason=reason)
            _receipt("step", session_id, procedure=proc.get("name"), step=step_no,
                     ok=False, source="transcript")
            recorded += 1
        tr.write_cursor(local_root, cursor)
    except Exception:
        return ""        # bookkeeping must never disturb the session
    return f"; {recorded} failure(s) from the transcript" if recorded else ""


def cmd_auto_outcome(args):
    """Hook handler — Claude Code PostToolUse on Bash.

    The other half of the policy gate. `auto-policy` asks about a workflow
    whose record is weak; this is what gives a workflow a record at all. When
    a command is recognisably one step of a learned procedure, its exit code
    is written back to that step. Without this every procedure stays
    `untested` forever, and the gate is an opinion with no evidence under it.

    Deliberately quiet and deliberately reluctant: it writes only when the
    step names the same tool the command ran and shares a word beyond it, and
    it records nothing at all when the outcome is unclear. It prints nothing
    and exits 0 whatever happens, because a bookkeeping hook must never
    disturb the session it is observing.
    """
    HOOK = "auto-outcome"
    EVENT = "PostToolUse"
    from cloud import policy

    def _exit(status):
        _emit_hook_exit(EVENT, args, HOOK, status)

    try:
        try:
            input_data = json.loads(sys.stdin.read())
        except Exception:
            _exit("no input")
        if input_data.get("tool_name") != "Bash":
            _exit("skipped (not Bash)")
        command = (input_data.get("tool_input") or {}).get("command") or ""
        if not command:
            _exit("no command")
        local_root = _local_dir(args)
        if not local_root:
            # The cloud endpoint records whole runs: it always moves the
            # procedure's own counters. One shell command is not a run, and
            # writing it there would inflate the very record the gate reads.
            # Until the API takes a step-scoped write, the folder is where
            # this loop closes.
            _exit("cloud mode: step-scoped feedback not available yet")

        procs = policy.memfmt_procedures(str(local_root))
        store = _local_store(local_root)
        tool_use_id = input_data.get("tool_use_id")

        # Failures never arrive as an event — this hook does not fire for them.
        # Two readers find them, in order. First the intents the gate noted at
        # PreToolUse, resolved by id against their transcripts: exact, and it
        # works across sessions. Then the transcript sweep, for commands that
        # ran without an intent on record. Both before anything about *this*
        # command is decided, because a failure is not about this command:
        # skipping it whenever the current call happens to be an `ls` would
        # leave failures unrecorded for as long as the session stayed quiet.
        from local import intents
        from local import transcript as tr
        cursor = tr.read_cursor(local_root)
        seen = list(cursor.get("seen") or [])
        settled = _settle_open_intents(local_root, store, tool_use_id, seen)
        if settled:
            cursor["seen"] = seen
            tr.write_cursor(local_root, cursor)
        failed = _record_transcript_failures(
            input_data.get("transcript_path"), local_root, procs, store,
            input_data.get("session_id")) + settled

        # Now this command. Its intent, if the gate noted one, names the step;
        # no second match. Otherwise a wider net than the gate upstream: the
        # gate stays narrow because a false question interrupts a human, while
        # a recording costs nothing and evidence is what is scarce. A command
        # naming no known tool cannot match any step.
        intent = intents.close_intent(local_root, tool_use_id) if tool_use_id else None
        if intent is None and not policy.shell_verbs(command):
            _exit(f"skipped (no known tool in the command){failed}")
        worked = _bash_outcome(input_data.get("tool_response"))
        if worked is None:
            _exit(f"outcome unclear — nothing recorded{failed}")

        if intent is not None:
            name, step_no = intent.get("procedure") or "unnamed workflow", int(intent.get("step") or 0)
        else:
            hit = policy.best_step_match(procs, command)
            if hit is None:
                _exit(f"no step matched{failed}")
            proc, step_no, _score = hit
            name = proc.get("name") or "unnamed workflow"
        reason = None if worked else _failure_reason(input_data.get("tool_response"))
        store.step_outcome(name, step_no, success=worked, reason=reason)
        _receipt("step", input_data.get("session_id"), procedure=name, step=step_no,
                 ok=bool(worked), source="event")
        _exit(f"'{name}' step {step_no} recorded as "
              f"{'success' if worked else 'failure'}{failed}")

    except SystemExit:
        raise
    except Exception:
        _exit("error")


def _resolve_mengram_bin() -> str:
    """The program to write into a hook command.

    A bare `mengram` is found only when the install put it on PATH, and a
    user-site install on macOS does not. The hook then runs in a shell that
    never loads a profile, so it fails — and every hook is built to fail
    silently, which is how an install can report success and do nothing for
    months. The most reliable answer is the script that is running right now:
    it demonstrably launched, so writing its path down cannot be wrong.
    """
    candidate = sys.argv[0] or ""
    try:
        path = Path(candidate).resolve()
    except (OSError, ValueError):
        path = None
    if (path and path.is_file() and os.access(path, os.X_OK)
            and path.name.startswith("mengram")):
        return str(path)
    return shutil.which("mengram") or "mengram"


def _shell_quote(path: str) -> str:
    return f'"{path}"' if " " in path else path


def _broken_hook_commands() -> list[tuple[str, str]]:
    """Installed Mengram hooks that a shell cannot run: `[(command, why)]`.

    Empty when nothing is installed. Not installed is a choice; installed and
    unable to launch is the failure worth shouting about.
    """
    try:
        settings_path = get_claude_code_settings_path()
        settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    except Exception:
        return []
    out = []
    for groups in (settings.get("hooks") or {}).values():
        for group in groups or []:
            for hook in group.get("hooks", []) or []:
                cmd = hook.get("command", "")
                if "mengram" not in cmd or "auto-" not in cmd:
                    continue
                ok, detail = _hook_command_runs(cmd)
                if not ok:
                    out.append((cmd, detail))
    return out


def _hook_command_runs(command: str) -> tuple[bool, str]:
    """Can a shell actually run this hook command? `(ok, detail)`.

    Asked the way Claude Code asks it — through a non-interactive shell, which
    does not read the profile that makes `mengram` resolvable in a terminal.
    That gap is the whole bug: a check that only reads settings.json reports a
    healthy install that has never run once.
    """
    import subprocess
    try:
        r = subprocess.run(f"{command} --help", shell=True, capture_output=True,
                           text=True, timeout=20)
    except Exception as e:
        return False, type(e).__name__
    if r.returncode == 0:
        return True, ""
    lines = (r.stderr or r.stdout or "").strip().splitlines()
    return False, (lines[-1][:120] if lines else f"exit {r.returncode}")


def cmd_hook(args):
    """Manage Claude Code auto-save hook"""
    action = getattr(args, "hook_action", None)
    if action == "install":
        cmd_hook_install(args)
    elif action == "uninstall":
        cmd_hook_uninstall(args)
    elif action == "status":
        cmd_hook_status(args)
    else:
        print("Usage: mengram hook {install,uninstall,status}")
        print("  mengram hook install           Install auto-save hook")
        print("  mengram hook install --every 5  Save every 5th response")
        print("  mengram hook uninstall         Remove auto-save hook")
        print("  mengram hook status            Check hook status")
        sys.exit(1)


def _upsert_hook(settings, event_name, command_marker, hook_def, matcher=None):
    """Insert or update a hook in settings[hooks][event_name] matching command_marker.

    `matcher` (a tool-name regex such as "Bash") is set only when the group is
    created; an existing group keeps whatever matcher the user gave it."""
    if "hooks" not in settings:
        settings["hooks"] = {}
    if event_name not in settings["hooks"]:
        settings["hooks"][event_name] = []

    found = False
    for group in settings["hooks"][event_name]:
        hooks_list = group.get("hooks", [])
        for i, hook in enumerate(hooks_list):
            if command_marker in hook.get("command", ""):
                hooks_list[i] = hook_def
                found = True
                break
        if found:
            break

    if not found:
        group = {"hooks": [hook_def]}
        if matcher:
            group = {"matcher": matcher, "hooks": [hook_def]}
        settings["hooks"][event_name].append(group)

    return found


def _remove_hook(settings, event_name, command_marker):
    """Remove hooks matching command_marker from settings[hooks][event_name]."""
    hooks_list = settings.get("hooks", {}).get(event_name, [])
    if not hooks_list:
        return False

    new_list = []
    removed = False
    for group in hooks_list:
        hl = group.get("hooks", [])
        filtered = [h for h in hl if command_marker not in h.get("command", "")]
        if len(filtered) < len(hl):
            removed = True
        if filtered:
            group["hooks"] = filtered
            new_list.append(group)

    settings["hooks"][event_name] = new_list
    if not settings["hooks"][event_name]:
        del settings["hooks"][event_name]
    if not settings["hooks"]:
        del settings["hooks"]

    return removed


def _ssl_context():
    """Build an SSL context that uses certifi CAs when available, otherwise
    falls back to the system trust store. macOS system Python sometimes ships
    without a usable CA bundle, so certifi is the safer default."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _cli_user_agent() -> str:
    """User-Agent for HTTP requests from the CLI. Cloudflare rejects the
    default `Python-urllib/X.Y` UA with error 1010 — always send a real one."""
    try:
        from importlib.metadata import version as _pkg_version
        ver = _pkg_version("mengram-ai")
    except Exception:
        ver = "dev"
    return f"Mengram-CLI/{ver}"


def _api_request_unauth(method, path, body=None):
    """Unauthenticated HTTP request to Mengram API (for signup/verify)."""
    import urllib.request
    import urllib.error
    base = os.environ.get("MENGRAM_URL", "https://mengram.io").rstrip("/")
    url = base + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Content-Type": "application/json",
            "User-Agent": _cli_user_agent(),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as resp:
            return json.loads(resp.read()), resp.status
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read()), e.code
        except Exception:
            return {"detail": str(e)}, e.code
    except Exception as e:
        return {"detail": f"Cannot connect to mengram.io: {e}"}, 0


def _save_api_key(api_key):
    """Save API key to shell profile (~/.zshrc or ~/.bashrc)."""
    shell = os.environ.get("SHELL", "/bin/bash")
    if "zsh" in shell:
        profile = Path.home() / ".zshrc"
    else:
        profile = Path.home() / ".bashrc"

    export_line = f'export MENGRAM_API_KEY="{api_key}"'

    try:
        content = profile.read_text() if profile.exists() else ""

        if "MENGRAM_API_KEY" in content:
            import re
            lines = content.split("\n")
            lines = [export_line if re.match(r'^\s*export\s+MENGRAM_API_KEY=', l) else l for l in lines]
            profile.write_text("\n".join(lines))
        else:
            with open(profile, "a") as f:
                f.write(f"\n# Mengram AI memory\n{export_line}\n")

        os.environ["MENGRAM_API_KEY"] = api_key
        return profile
    except Exception:
        os.environ["MENGRAM_API_KEY"] = api_key
        return None


def _cloud_config_path() -> Path:
    return DEFAULT_HOME / "config.json"


def _save_cloud_config(api_key: str) -> Path:
    """Persist API key to ~/.mengram/config.json (agent-readable location)."""
    DEFAULT_HOME.mkdir(parents=True, exist_ok=True)
    path = _cloud_config_path()
    existing = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except Exception:
            existing = {}
    existing["api_key"] = api_key
    existing["base_url"] = os.environ.get("MENGRAM_URL", "https://mengram.io").rstrip("/")
    path.write_text(json.dumps(existing, indent=2))
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return path


def _load_cloud_api_key() -> str:
    """Resolve API key in this order: env var, ~/.mengram/config.json."""
    key = os.environ.get("MENGRAM_API_KEY", "").strip()
    if key:
        return key
    path = _cloud_config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return (data.get("api_key") or "").strip()
        except Exception:
            return ""
    return ""


def _load_cloud_base_url() -> str:
    """Resolve base URL in this order: env var, ~/.mengram/config.json, default."""
    url = os.environ.get("MENGRAM_URL", "").strip()
    if url:
        return url.rstrip("/")
    path = _cloud_config_path()
    if path.exists():
        try:
            data = json.loads(path.read_text())
            cfg_url = (data.get("base_url") or "").strip()
            if cfg_url:
                return cfg_url.rstrip("/")
        except Exception:
            pass
    return "https://mengram.io"


def _hook_marker(hook_name: str, status: str) -> str:
    """Build a one-line status marker for verbose hook output."""
    return f"[mengram:{hook_name}] {status}"


def _emit_hook_exit(hook_event_name, args, hook_name, status, context=None, system_message=None):
    """Emit a Claude Code hook JSON response and exit(0).

    Non-verbose (default): preserves prior silent behavior — suppressOutput
    when there's no context, or just the additionalContext when there is.

    Verbose (--verbose): adds a one-line status marker via `systemMessage`
    (shown to the user for any hook type), and for UserPromptSubmit /
    SessionStart also prefixes it onto `additionalContext` so Claude sees it.
    Stop hooks don't support additionalContext, so verbose Stop output is
    systemMessage only.
    """
    verbose = getattr(args, "verbose", False)

    # Cursor's hooks speak a flatter dialect: `{"additional_context": ...}` at
    # the top level is the only thing a hook may say back, and there is no
    # systemMessage. Anything else is `{}`.
    if getattr(args, "host", None) == "cursor":
        print(json.dumps({"additional_context": context} if context else {}))
        sys.exit(0)

    payload = {"continue": True}

    if context:
        payload["hookSpecificOutput"] = {
            "hookEventName": hook_event_name,
            "additionalContext": context,
        }

    if verbose:
        marker = _hook_marker(hook_name, status)
        payload["systemMessage"] = marker
        if hook_event_name in ("UserPromptSubmit", "SessionStart"):
            existing = payload.get("hookSpecificOutput", {}).get("additionalContext", "")
            payload["hookSpecificOutput"] = {
                "hookEventName": hook_event_name,
                "additionalContext": marker + (("\n\n" + existing) if existing else ""),
            }
    elif not context:
        payload["suppressOutput"] = True

    # User-facing message (e.g. the weekly report) — shown to the human, never
    # added to Claude's context. Appended after any verbose marker.
    if system_message:
        prior = payload.get("systemMessage", "")
        payload["systemMessage"] = (prior + "\n\n" + system_message) if prior else system_message
        payload.pop("suppressOutput", None)

    print(json.dumps(payload))
    sys.exit(0)


def _save_and_report_key(api_key: str, label: str) -> None:
    """Persist API key to ~/.mengram/config.json + shell profile, print success.
    Shared by all CLI paths that successfully obtain a key."""
    cfg_path = _save_cloud_config(api_key)
    profile = _save_api_key(api_key)
    print(f"API key: {api_key}")
    print(f"Saved to: {cfg_path}")
    if profile:
        print(f"Shell profile updated: {profile}")
    print(f"Configured: yes ({label})")


def cmd_signup(args):
    """Non-interactive signup for agent-driven installs.

    Three response modes (server-driven):
      • Self-hosted with DISABLE_EMAIL_VERIFICATION=true: POST /v1/signup returns
        api_key immediately → save + exit success (no code prompt).
      • Hosted with email verification: returns "code sent" message → user runs
        again with --code Y to complete.
      • Existing account on hosted: 409 → reset-key flow; also returns api_key
        immediately on self-hosted, otherwise sends a reset code.

    See GitHub issue #38 — earlier version printed "Code sent" unconditionally
    on 200, which broke self-hosted installs that already returned the key.
    """
    email = (getattr(args, "email", "") or "").strip()
    code = (getattr(args, "code", "") or "").strip()

    if not email:
        print("Error: --email is required", file=sys.stderr)
        sys.exit(2)

    if not code:
        # Mode 1: trigger code email (or direct key on self-hosted)
        data, status = _api_request_unauth("POST", "/v1/signup", {"email": email})
        if status == 200:
            # Self-hosted direct-key path: server already returned the key.
            direct_key = (data.get("api_key") or "").strip() if isinstance(data, dict) else ""
            if direct_key:
                _save_and_report_key(direct_key, "self-hosted, no email verification")
                sys.exit(0)
            # Hosted path: server sent an email with a 6-digit code.
            print(f"Code sent to {email}. Run again with --code <6-digit-code> to complete signup.")
            sys.exit(0)
        if status == 409:
            # Existing account — try reset-key. On self-hosted this returns the key
            # immediately; on hosted it sends a reset-code email.
            data, status = _api_request_unauth("POST", "/v1/reset-key", {"email": email})
            if status == 200:
                direct_key = (data.get("api_key") or "").strip() if isinstance(data, dict) else ""
                if direct_key:
                    _save_and_report_key(direct_key, "self-hosted reset, no email verification")
                    sys.exit(0)
                print(f"Account exists. Reset-key code sent to {email}. Run again with --code <6-digit-code> to issue a new key.")
                sys.exit(0)
            print(f"Error: {data.get('detail', 'reset-key failed')}", file=sys.stderr)
            sys.exit(1)
        print(f"Error: {data.get('detail', 'signup failed')}", file=sys.stderr)
        sys.exit(1)

    # Mode 2: verify code. Try /v1/verify first; fall back to reset-verify on 4xx.
    data, status = _api_request_unauth("POST", "/v1/verify", {"email": email, "code": code})
    if status != 200:
        data2, status2 = _api_request_unauth("POST", "/v1/reset-key/verify", {"email": email, "code": code})
        if status2 == 200:
            data, status = data2, status2

    if status != 200:
        print(f"Error: {data.get('detail', 'verification failed')}", file=sys.stderr)
        sys.exit(1)

    api_key = (data.get("api_key") or "").strip()
    if not api_key:
        print("Error: API response missing api_key", file=sys.stderr)
        sys.exit(1)

    _save_and_report_key(api_key, "verified")


def cmd_doctor(args):
    """End-to-end round-trip test: add a memory and search it back.

    Exits 0 on success, 1 on failure. Output ends with one of:
      OK: round-trip succeeded.
      FAIL: <reason>
    """
    import urllib.request
    import urllib.error
    import time

    # Hooks first. A cloud round-trip says nothing about whether the hooks that
    # actually do the work can launch, and reporting OK while they cannot is
    # how an install stayed dead for months without anyone noticing.
    broken = _broken_hook_commands()
    if broken:
        print("FAIL: Claude Code hooks are installed but cannot run.", file=sys.stderr)
        for cmd, detail in broken:
            print(f"  {cmd}\n    -> {detail}", file=sys.stderr)
        print("  Run `mengram hook install` again to write the full path.", file=sys.stderr)
        sys.exit(1)

    api_key = _load_cloud_api_key()
    if not api_key:
        print("FAIL: no API key. Run `mengram signup --email <you>` first.", file=sys.stderr)
        sys.exit(1)

    base = _load_cloud_base_url().rstrip("/")
    marker = f"mengram-doctor-{int(time.time())}"
    fact_text = f"Round-trip marker {marker}: this memory was written by mengram doctor."

    ctx = _ssl_context()
    ua = _cli_user_agent()

    def _req(method, path, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            base + path, data=data, method=method,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "User-Agent": ua,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
                return json.loads(r.read()), r.status
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read()), e.code
            except Exception:
                return {"detail": str(e)}, e.code
        except Exception as e:
            return {"detail": str(e)}, 0

    print("0/3  Checking MCP SDK presence ...")
    try:
        import mcp  # noqa: F401
        print("     ok — mcp package importable")
    except ImportError:
        print("FAIL: `mcp` package is not installed. The MCP server (`mengram "
              "server --cloud`) will not start without it.", file=sys.stderr)
        print("     Fix: pip install --user 'mcp>=1.0'   (or reinstall: pip "
              "install --user --upgrade mengram-ai)", file=sys.stderr)
        sys.exit(1)

    print("1/3  Authenticating against /v1/me ...")
    try:
        req = urllib.request.Request(
            base + "/v1/me",
            headers={
                "Authorization": f"Bearer {api_key}",
                "User-Agent": ua,
            },
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            me = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"FAIL: auth check returned HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"FAIL: cannot reach {base}/v1/me ({e})", file=sys.stderr)
        sys.exit(1)
    print(f"     ok — account {me.get('email', '<unknown>')} plan={me.get('plan', '?')}")

    print("2/3  Writing test memory via /v1/add ...")
    body = {"messages": [{"role": "user", "content": fact_text}]}
    data, status = _req("POST", "/v1/add", body)
    if status not in (200, 202):
        print(f"FAIL: /v1/add returned HTTP {status}: {data.get('detail', '<no detail>')}", file=sys.stderr)
        sys.exit(1)
    job_id = data.get("job_id")
    print(f"     ok — job_id={job_id} (async; waiting for extraction)")

    print("3/3  Searching for marker via /v1/search/all ...")
    found = False
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(3)
        data, status = _req("POST", "/v1/search/all", {"query": marker, "limit": 5})
        if status != 200:
            print(f"FAIL: /v1/search/all returned HTTP {status}: {data.get('detail', '<no detail>')}", file=sys.stderr)
            sys.exit(1)
        results = data.get("results") or []
        for r in results:
            blob = json.dumps(r)
            if marker in blob:
                found = True
                break
        if found:
            break

    if not found:
        print("FAIL: marker did not appear in search within 45s.")
        print("     The memory may still be processing — extraction can take longer under load.")
        print("     Re-run `mengram doctor` in a minute; if it keeps failing, contact support.")
        sys.exit(1)

    print("OK: round-trip succeeded.")


def _render_weekly(stats: dict, week_label: str, color: bool = True) -> str:
    """Render the weekly memory report box (<=58 cols, screenshot-friendly).

    Spec: ccusage-style box-drawing, no emoji inside boxes, hero block =
    repeated mistakes prevented with 'last bitten' dates.
    """
    W = 58
    BLUE = "\033[34m" if color else ""
    YELLOW = "\033[1;33m" if color else ""
    GREEN = "\033[32m" if color else ""
    DIM = "\033[2m" if color else ""
    R = "\033[0m" if color else ""

    def pad(s: str, n: int) -> str:
        return s + " " * max(0, n - len(s))

    lines = []
    title = f"MENGRAM · Your AI's memory — {week_label}"
    lines.append("╭" + "─" * (W - 2) + "╮")
    lines.append("│" + " " * (W - 2) + "│")
    lines.append("│   " + BLUE + pad(title, W - 8) + R + "   │")
    lines.append("│" + " " * (W - 2) + "│")
    lines.append("╰" + "─" * (W - 2) + "╯")
    lines.append("")

    facts = stats.get("facts_learned", 0)
    prev = stats.get("facts_prev_week", 0)
    delta = facts - prev
    delta_s = (GREEN + f"▲ {delta}" + R) if delta > 0 else (DIM + f"▼ {abs(delta)}" + R) if delta < 0 else (DIM + "=" + R)
    lines.append(f"  Facts learned        {facts:>5}    {delta_s} vs last week")

    procs = stats.get("procedures_learned", 0)
    bump = stats.get("latest_version_bump")
    bump_s = ""
    if bump:
        v = bump.get("version", 1)
        bump_s = f"{bump.get('name', '')[:24]} v{v - 1} → v{v}"
    lines.append(f"  Procedures learned   {procs:>5}    " + DIM + bump_s + R)

    recalls = stats.get("recalls_served", 0)
    lines.append(f"  Recalls served       {recalls:>5}")
    lines.append("")

    prevented = stats.get("prevented", [])
    n = len(prevented)
    lines.append("┌" + "─" * (W - 2) + "┐")
    header = pad("  REPEATED MISTAKES PREVENTED", W - 12) + f"{n:>4}"
    lines.append("│" + YELLOW + pad(header, W - 2) + R + "│")
    if prevented:
        lines.append("│" + " " * (W - 2) + "│")
        for p in prevented:
            name = (p.get("name") or "?")[:28]
            bitten = p.get("last_bitten")
            if bitten:
                try:
                    import datetime as _dt
                    bitten = _dt.date.fromisoformat(bitten).strftime("%b %d")
                except (ValueError, TypeError):
                    pass
                tail = f"last bitten: {bitten}"
            else:
                tail = f"{p.get('fail_count', 0)} past failures"
            row = f"  · {pad(name, 28)} {tail}"
            lines.append("│" + pad(row, W - 2)[: W - 2] + "│")
    else:
        lines.append("│" + pad("  0 — clean week", W - 2) + "│")
    lines.append("└" + "─" * (W - 2) + "┘")
    lines.append("")
    lines.append(DIM + "  Share it: mengram weekly --share" + R)
    return "\n".join(lines)


def cmd_weekly(args):
    """Print the weekly memory report."""
    import datetime as _dt

    api_key = _load_cloud_api_key()
    if not api_key:
        print("No API key. Run `mengram setup` first.", file=sys.stderr)
        sys.exit(1)
    from cloud.client import CloudMemory
    mem = CloudMemory(api_key=api_key, base_url=_load_cloud_base_url())
    user_id = getattr(args, "user_id", None) or "default"
    stats = mem.weekly_stats(user_id=user_id)

    today = _dt.date.today()
    start = today - _dt.timedelta(days=6)
    week_label = f"week of {start.strftime('%b %d')}–{today.strftime('%d')}"
    color = sys.stdout.isatty() and not getattr(args, "no_color", False)
    print(_render_weekly(stats, week_label, color=color))

    if getattr(args, "share", False):
        n = len(stats.get("prevented", []))
        facts = stats.get("facts_learned", 0)
        recalls = stats.get("recalls_served", 0)
        post = (f"My AI stopped me from repeating {n} old mistake{'s' if n != 1 else ''} this week. "
                f"{facts} facts, {recalls} recalls. It remembers what I don't. 🧠 mengram.io")
        print("\n--- ready to post ---\n" + post)
        try:
            import subprocess
            subprocess.run(["pbcopy"], input=post.encode(), check=True)
            print("(copied to clipboard)")
        except Exception:
            pass


def _ask_yes(prompt: str, default: bool = True) -> bool:
    """input() with a default that survives non-interactive runs (agents, pipes)."""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        if not sys.stdin.isatty():
            return default
        answer = input(prompt + suffix).strip().lower()
    except EOFError:
        return default
    if not answer:
        return default
    return answer in ("y", "yes")


def _mcp_server_entry(api_key: str) -> dict:
    """Canonical MCP config entry — same shape as the landing docs."""
    mengram_bin = shutil.which("mengram") or "mengram"
    return {
        "command": mengram_bin,
        "args": ["server", "--cloud"],
        "env": {
            "MENGRAM_API_KEY": api_key,
            "MENGRAM_URL": _load_cloud_base_url(),
        },
    }


def _write_mcp_config(config_path: Path, entry: dict) -> str:
    """Merge mengram into an MCP config file. Returns: written | already | corrupt."""
    config = {}
    if config_path.exists():
        try:
            with open(config_path) as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError):
            return "corrupt"  # never clobber a file we can't parse
    servers = config.setdefault("mcpServers", {})
    if "mengram" in servers:
        return "already"
    if config_path.exists():
        try:
            shutil.copy2(config_path, str(config_path) + ".bak-mengram")
        except OSError:
            pass
    servers["mengram"] = entry
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)
    return "written"


def _detect_mcp_tools() -> list:
    """Detect installed AI tools that take an MCP config. Returns [(name, config_path)]."""
    tools = []
    if (Path.home() / ".cursor").exists():
        tools.append(("Cursor", Path.home() / ".cursor" / "mcp.json"))
    claude_desktop = get_claude_desktop_config_path()
    if claude_desktop.parent.exists():
        tools.append(("Claude Desktop", claude_desktop))
    windsurf_dir = Path.home() / ".codeium" / "windsurf"
    if windsurf_dir.exists():
        tools.append(("Windsurf", windsurf_dir / "mcp_config.json"))
    return tools


def cmd_setup(args):
    """Interactive signup + API key setup + hook install."""
    print("\n  Welcome to Mengram — AI memory for your apps\n")

    # Fast path: --key flag (for users who already have a key from the website)
    provided_key = getattr(args, "key", None)
    if provided_key:
        api_key = provided_key
        print(f"  API key: {api_key[:10]}...{api_key[-4:]}")
    else:
        # Check existing key
        existing_key = os.environ.get("MENGRAM_API_KEY", "")
        if existing_key:
            answer = input("  Already configured. Reconfigure? [y/N]: ").strip().lower()
            if answer != "y":
                print("  Keeping existing configuration.")
                return
            print()

        # Get email
        email = getattr(args, "email", None)
        if not email:
            email = input("  Email: ").strip()
        if not email:
            print("  Email is required.")
            return

        # Step 1: Send verification code
        data, status = _api_request_unauth("POST", "/v1/signup", {"email": email})

        is_reset = False
        if status == 409:
            # Already registered — offer key reset
            print("  Email already registered.")
            answer = input("  Reset API key? [y/N]: ").strip().lower()
            if answer != "y":
                print("\n  To use your existing key:")
                print('  export MENGRAM_API_KEY="om-your-key"')
                print("  mengram hook install\n")
                return
            data, status = _api_request_unauth("POST", "/v1/reset-key", {"email": email})
            if status != 200:
                print(f"  Error: {data.get('detail', 'Unknown error')}")
                return
            is_reset = True
            print("  Verification code sent! Check your inbox.\n")
        elif status == 200:
            print("  Verification code sent! Check your inbox.\n")
        else:
            print(f"  Error: {data.get('detail', 'Cannot connect to mengram.io')}")
            return

        # Step 2: Verify code
        verify_path = "/v1/reset-key/verify" if is_reset else "/v1/verify"
        for attempt in range(3):
            code = input("  Code: ").strip()
            if not code:
                continue
            data, status = _api_request_unauth("POST", verify_path, {"email": email, "code": code})
            if status == 200:
                break
            print(f"  {data.get('detail', 'Invalid code.')} Try again.")
        else:
            print("  Too many attempts. Run 'mengram setup' to start over.")
            return

        api_key = data.get("api_key", "")
        if not api_key:
            print("  Error: no API key in response.")
            return

        if is_reset:
            print("  New API key generated!\n")
        else:
            print("  Account created!\n")

        print(f"  API key: {api_key}")

    # Save key to shell profile and to ~/.mengram/config.json (agent-readable).
    profile = _save_api_key(api_key)
    if profile:
        print(f"  Key saved to {profile}")
    else:
        print(f"  Could not write to shell profile. Add manually:")
        print(f'  export MENGRAM_API_KEY="{api_key}"')
    try:
        cfg_path = _save_cloud_config(api_key)
        print(f"  Key persisted to {cfg_path}")
    except Exception as e:
        print(f"  Note: could not write ~/.mengram/config.json ({e})")

    # Install hooks
    no_hooks = getattr(args, "no_hooks", False)
    codex_done = False
    cursor_done = False
    if not no_hooks:
        try:
            os.environ["MENGRAM_API_KEY"] = api_key  # hook install reads env
            cmd_hook_install(args)
        except SystemExit:
            pass
        # Codex on this machine gets the same memory without a second command.
        # The welcome page shows one line for both tools and says so.
        if _codex_present():
            try:
                codex_args = argparse.Namespace(**{**vars(args), "codex": True})
                print("\n  Codex found on this machine — installing its hooks too.")
                cmd_hook_install(codex_args)
                codex_done = True
            except SystemExit:
                pass
            except Exception as e:
                print(f"  Codex hooks skipped ({e}) — run `mengram hook install --codex` later.")
        if _cursor_present():
            try:
                cursor_args = argparse.Namespace(**{**vars(args), "codex": False, "cursor": True})
                print("\n  Cursor found on this machine — installing its hooks too.")
                cmd_hook_install(cursor_args)
                cursor_done = True
            except SystemExit:
                pass
            except Exception as e:
                print(f"  Cursor hooks skipped ({e}) — run `mengram hook install --cursor` later.")
    else:
        print("\n  Skipped hook install (--no-hooks).")

    configured = []

    # Detect other AI tools and wire up MCP configs (Cursor, Claude Desktop, Windsurf)
    if not getattr(args, "no_tools", False):
        tools = _detect_mcp_tools()
        if tools:
            names = ", ".join(t[0] for t in tools)
            print(f"\n  Detected: {names}")
            if _ask_yes("  Connect Mengram memory to them too?"):
                entry = _mcp_server_entry(api_key)
                for name, path in tools:
                    result = _write_mcp_config(path, entry)
                    if result == "written":
                        configured.append(name)
                        print(f"  ✓ {name}: {path}")
                    elif result == "already":
                        print(f"  ✓ {name}: already configured")
                    else:
                        print(f"  ! {name}: could not parse {path} — skipped (add manually, see mengram.io/#install)")

    # Warm start: import existing Claude Code history so memory is useful from minute one
    imported = False
    if not getattr(args, "no_import", False):
        projects_dir = Path.home() / ".claude" / "projects"
        if projects_dir.exists() and any(projects_dir.iterdir()):
            print("\n  Found local Claude Code session history.")
            if _ask_yes("  Import your recent sessions so memory starts warm?"):
                import_args = argparse.Namespace(
                    import_type="claude-code", last=20, project="",
                    reimport=False, yes=True, user_id=None,
                )
                try:
                    cmd_import(import_args)
                    imported = True
                except SystemExit:
                    pass
                except Exception as e:
                    print(f"  Import skipped ({e}) — run `mengram import claude-code` later.")

    # Verify the round-trip end-to-end
    if not getattr(args, "no_verify", False):
        print("\n  Verifying round-trip ...")
        mengram_bin = shutil.which("mengram")
        if mengram_bin:
            import subprocess
            try:
                r = subprocess.run([mengram_bin, "doctor"], capture_output=True, text=True, timeout=90)
                tail = (r.stdout.strip().splitlines() or [""])[-1]
                print(f"  {tail}" if tail.startswith("OK") else "  Verify inconclusive — run `mengram doctor` for details.")
            except Exception:
                print("  Verify skipped — run `mengram doctor` later.")

    restart = ["Claude Code"] + (["Codex"] if codex_done else []) + (["Cursor"] if cursor_done else []) + configured
    print("\n  Done! Restart " + ", ".join(restart) + " — it now remembers everything.")
    if imported:
        print('  Try asking: "What do you know about my projects?"')
    print()


def cmd_hook_install(args):
    """Install Claude Code memory hooks (auto-save + auto-recall + session context + policy gate + run outcomes)"""
    local_dir = _local_dir(args)
    # Env or ~/.mengram/config.json: `mengram setup --key` writes the file, and
    # `hook install --codex` run right after it must not ask for the env var.
    api_key = _load_cloud_api_key()
    if not local_dir and not api_key:
        print("No API key: set MENGRAM_API_KEY or save one to ~/.mengram/config.json", file=sys.stderr)
        print("Run 'mengram setup' to create an account and configure automatically", file=sys.stderr)
        print("Or get a key at: https://mengram.io/#signup", file=sys.stderr)
        print("No account? Use a folder instead: mengram hook install --memory ./memory", file=sys.stderr)
        sys.exit(1)
    if local_dir and not local_dir.is_dir():
        print(f"no memory folder at {local_dir} — run: mengram local init {local_dir}", file=sys.stderr)
        sys.exit(1)

    every = getattr(args, "every", 3) or 3
    user_id = getattr(args, "user_id", None)

    # Build hook commands
    # Absolute path, not a bare name: see _resolve_mengram_bin.
    mengram_bin = _resolve_mengram_bin()
    prog = _shell_quote(mengram_bin)
    save_cmd = f"{prog} auto-save --every {every}"
    recall_cmd = f"{prog} auto-recall"
    context_cmd = f"{prog} auto-context"
    policy_cmd = f"{prog} auto-policy"
    outcome_cmd = f"{prog} auto-outcome"
    checkpoint_cmd = f"{prog} auto-checkpoint"
    restore_cmd = f"{prog} auto-restore"
    if user_id:
        save_cmd += f" --user-id {user_id}"
        recall_cmd += f" --user-id {user_id}"
        context_cmd += f" --user-id {user_id}"
        policy_cmd += f" --user-id {user_id}"
        outcome_cmd += f" --user-id {user_id}"
    if local_dir:
        # Hooks run without the user's shell profile, so the folder travels
        # in the command rather than in an env var that may not be there.
        mem_arg = f' --memory "{local_dir.resolve()}"'
        save_cmd += mem_arg
        recall_cmd += mem_arg
        context_cmd += mem_arg
        policy_cmd += mem_arg
        outcome_cmd += mem_arg

    if getattr(args, "codex", False):
        _install_codex_hooks(context_cmd, recall_cmd, checkpoint_cmd, save_cmd, mengram_bin)
        return
    if getattr(args, "cursor", False):
        _install_cursor_hooks(context_cmd, checkpoint_cmd, restore_cmd, save_cmd, mengram_bin)
        return

    # Read existing settings
    settings_path = get_claude_code_settings_path()
    settings = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except (json.JSONDecodeError, Exception):
            settings = {}

    # 1. Stop hook — auto-save conversations (async, background)
    _upsert_hook(settings, "Stop", "mengram auto-save", {
        "type": "command",
        "command": save_cmd,
        "timeout": 30,
        "async": True,
    })

    # 2. UserPromptSubmit hook — recall relevant memories per prompt
    _upsert_hook(settings, "UserPromptSubmit", "mengram auto-recall", {
        "type": "command",
        "command": recall_cmd,
        "timeout": 10,
    })

    # 3. SessionStart hook — load cognitive profile on session start + after compaction
    _upsert_hook(settings, "SessionStart", "mengram auto-context", {
        "type": "command",
        "command": context_cmd,
        "timeout": 15,
    })

    # 4. PreToolUse hook — a Bash command that matches a learned workflow with
    #    a weak record (never run, inherited only, or below the bar) is turned
    #    into a confirmation prompt instead of running on the agent's say-so.
    if not getattr(args, "no_policy", False):
        _upsert_hook(settings, "PreToolUse", "mengram auto-policy", {
            "type": "command",
            "command": policy_cmd,
            "timeout": 10,
        }, matcher="Bash")

    # 5. PostToolUse hook — write back what actually happened, so the gate above
    #    has a record to judge by instead of an empty one. Observation only.
    _upsert_hook(settings, "PostToolUse", "mengram auto-outcome", {
        "type": "command",
        "command": outcome_cmd,
        "timeout": 10,
    }, matcher="Bash")

    # 6. PreCompact hook — write the working state down before the host
    #    summarises it; SessionStart (source: compact) reads it back verbatim.
    _upsert_hook(settings, "PreCompact", "mengram auto-checkpoint", {
        "type": "command",
        "command": checkpoint_cmd,
        "timeout": 10,
    })

    # Write settings
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    print("Mengram hooks installed" + (f" (local mode: {local_dir.resolve()})" if local_dir else "") + ":")
    print(f"  Auto-save:    every {every} response(s) (background)")
    print(f"  Auto-recall:  search memory on each prompt")
    print(f"  Session context: load profile on session start")
    if not getattr(args, "no_policy", False):
        print(f"  Policy gate:  confirm before running a workflow with a weak record")
    print(f"  Run outcomes: record whether a workflow's step worked")
    print(f"  Checkpoint:   save the working state before compaction, restore it after")
    print(f"  Settings: {settings_path}")

    # Verify rather than assume. An install that cannot run is the failure
    # this whole path is here to stop being invisible.
    ok, detail = _hook_command_runs(save_cmd)
    if ok:
        print(f"  Verified: {mengram_bin} runs from a plain shell")
    else:
        print(f"\n  WARNING: the hook command does not run: {detail}")
        print(f"  Claude Code would launch: {save_cmd}")
        if mengram_bin == "mengram":
            print("  `mengram` is not on PATH here, so the hooks would do nothing,")
            print("  silently. Reinstall with the full path, or add the directory")
            print("  holding the `mengram` script to PATH and run this again.")
        print("  Nothing else in Claude Code is affected.")

    print(f"\nRestart Claude Code for hooks to take effect.")


def get_codex_hooks_path() -> Path:
    """Codex reads lifecycle hooks from `~/.codex/hooks.json` (or `CODEX_HOME`).
    Inside Orca, CODEX_HOME is Orca's runtime copy, rebuilt from ~/.codex on
    launch; hooks written there would be dropped, so the real home is used."""
    home = os.environ.get("CODEX_HOME")
    if home and home == os.environ.get("ORCA_CODEX_HOME"):
        home = None
    return Path(home or (Path.home() / ".codex")) / "hooks.json"


def _codex_present() -> bool:
    """Codex has been run on this machine: its home exists or its binary is on PATH."""
    home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    return home.is_dir() or shutil.which("codex") is not None


# ---- Cursor ----------------------------------------------------------------
# Cursor's hooks (cursor.com/docs/hooks) fire the same moments as Claude Code's
# but the file is flatter: `{"version": 1, "hooks": {"sessionStart": [{"command":
# ..., "timeout": ...}]}}`, no matcher groups, no "type". A hook gets the
# conversation_id, workspace_roots and transcript_path on stdin and may answer
# `{"additional_context": ...}` on sessionStart and postToolUse — the two doors
# memory can walk through. `beforeSubmitPrompt` can only allow or block, so
# there is no per-prompt recall; mid-conversation recall stays on request via MCP.

def get_cursor_hooks_path() -> Path:
    return Path(os.environ.get("CURSOR_HOME") or (Path.home() / ".cursor")) / "hooks.json"


def _cursor_present() -> bool:
    home = Path(os.environ.get("CURSOR_HOME") or (Path.home() / ".cursor"))
    return home.is_dir() or shutil.which("cursor") is not None


def _cursor_input(input_data: dict) -> dict:
    """Cursor's stdin fields in the names the handlers already use."""
    roots = input_data.get("workspace_roots") or []
    cwd = input_data.get("cwd") or (roots[0] if roots else None)
    out = dict(input_data)
    out["session_id"] = input_data.get("session_id") or input_data.get("conversation_id")
    out["cwd"] = cwd
    # afterAgentResponse hands the final text as `text`; Stop-style handlers
    # look for `last_assistant_message`.
    if "text" in input_data and "last_assistant_message" not in input_data:
        out["last_assistant_message"] = input_data.get("text") or ""
    return out


def _upsert_flat_hook(hooks: dict, event: str, marker: str, entry: dict) -> bool:
    """Cursor's per-event list is flat: insert or replace the entry whose
    command carries `marker`."""
    items = hooks.setdefault(event, [])
    for i, h in enumerate(items):
        if marker in str(h.get("command", "")):
            items[i] = entry
            return True
    items.append(entry)
    return False


def _remove_flat_hook(hooks: dict, event: str, marker: str) -> bool:
    items = hooks.get(event) or []
    kept = [h for h in items if marker not in str(h.get("command", ""))]
    if len(kept) == len(items):
        return False
    if kept:
        hooks[event] = kept
    else:
        hooks.pop(event, None)
    return True


def _install_cursor_hooks(context_cmd, checkpoint_cmd, restore_cmd, save_cmd, mengram_bin):
    hooks_path = get_cursor_hooks_path()
    data = {}
    if hooks_path.exists():
        try:
            with open(hooks_path) as f:
                data = json.load(f)
        except Exception:
            data = {}
    data.setdefault("version", 1)
    hooks = data.setdefault("hooks", {})
    _upsert_flat_hook(hooks, "sessionStart", "mengram auto-context",
                      {"command": context_cmd + " --host cursor", "timeout": 15})
    _upsert_flat_hook(hooks, "preCompact", "mengram auto-checkpoint",
                      {"command": checkpoint_cmd + " --host cursor", "timeout": 10})
    # After compaction Cursor opens no new session, so the checkpoint comes
    # back on the first tool call after it: postToolUse may add context.
    _upsert_flat_hook(hooks, "postToolUse", "mengram auto-restore",
                      {"command": restore_cmd + " --host cursor", "timeout": 5})
    _upsert_flat_hook(hooks, "afterAgentResponse", "mengram auto-save",
                      {"command": save_cmd + " --host cursor", "timeout": 30})
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(hooks_path, "w") as f:
        json.dump(data, f, indent=2)

    print("Mengram hooks installed for Cursor:")
    print("  Session context: load profile on a new conversation (and the last working state in this workspace)")
    print("  Checkpoint:      save the working state before compaction; put it back on the next tool call")
    print("  Auto-save:       save the agent's answer after each response")
    print("  Recall mid-conversation stays on request via MCP (Cursor has no per-prompt context hook)")
    print(f"  Hooks file: {hooks_path}")
    ok, detail = _hook_command_runs(context_cmd)
    if ok:
        print(f"  Verified: {mengram_bin} runs from a plain shell")
    else:
        print(f"\n  WARNING: the hook command does not run: {detail}")
        print(f"  Cursor would launch: {context_cmd}")
    print("\nRestart Cursor for hooks to take effect.")


def _uninstall_cursor_hooks() -> bool:
    hooks_path = get_cursor_hooks_path()
    if not hooks_path.exists():
        return False
    try:
        with open(hooks_path) as f:
            data = json.load(f)
    except Exception:
        return False
    hooks = data.get("hooks") or {}
    removed = False
    for event, marker in (("sessionStart", "mengram auto-context"), ("preCompact", "mengram auto-checkpoint"),
                          ("postToolUse", "mengram auto-restore"), ("afterAgentResponse", "mengram auto-save")):
        removed |= _remove_flat_hook(hooks, event, marker)
    if removed:
        with open(hooks_path, "w") as f:
            json.dump(data, f, indent=2)
    return removed


def _install_codex_hooks(context_cmd, recall_cmd, checkpoint_cmd, save_cmd, mengram_bin):
    """The same memory under Codex. Its hooks file has Claude Code's shape —
    events, matcher groups, `hookSpecificOutput.additionalContext` back — so
    the handlers are shared and only the file differs. Codex has no `timeout`
    key; it shows `statusMessage` while a hook runs."""
    hooks_path = get_codex_hooks_path()
    settings = {}
    if hooks_path.exists():
        try:
            with open(hooks_path) as f:
                settings = json.load(f)
        except Exception:
            settings = {}
    if not settings.get("description"):
        settings["description"] = "Mengram memory hooks"

    _upsert_hook(settings, "SessionStart", "mengram auto-context", {
        "type": "command", "command": context_cmd,
        "statusMessage": "Mengram: loading memory",
    })
    _upsert_hook(settings, "UserPromptSubmit", "mengram auto-recall", {
        "type": "command", "command": recall_cmd,
        "statusMessage": "Mengram: recalling",
    })
    _upsert_hook(settings, "PreCompact", "mengram auto-checkpoint", {
        "type": "command", "command": checkpoint_cmd + " --host codex",
        "statusMessage": "Mengram: saving working state",
    })
    _upsert_hook(settings, "Stop", "mengram auto-save", {
        "type": "command", "command": save_cmd + " --host codex",
        "statusMessage": "Mengram: saving",
    })

    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    with open(hooks_path, "w") as f:
        json.dump(settings, f, indent=2)

    print("Mengram hooks installed for Codex:")
    print("  Session context: load profile on session start")
    print("  Auto-recall:     search memory on each prompt")
    print("  Checkpoint:      save the working state before compaction, restore it after")
    print("  Auto-save:       save the turn and the task card when Codex stops")
    print(f"  Hooks file: {hooks_path}")
    ok, detail = _hook_command_runs(context_cmd)
    if ok:
        print(f"  Verified: {mengram_bin} runs from a plain shell")
    else:
        print(f"\n  WARNING: the hook command does not run: {detail}")
        print(f"  Codex would launch: {context_cmd}")
    print("\nRestart Codex for hooks to take effect.")


def _uninstall_codex_hooks() -> bool:
    hooks_path = get_codex_hooks_path()
    if not hooks_path.exists():
        return False
    try:
        with open(hooks_path) as f:
            settings = json.load(f)
    except Exception:
        return False
    removed = False
    for event, marker in (("SessionStart", "mengram auto-context"),
                          ("UserPromptSubmit", "mengram auto-recall"),
                          ("PreCompact", "mengram auto-checkpoint"),
                          ("Stop", "mengram auto-save")):
        removed |= _remove_hook(settings, event, marker)
    if removed:
        with open(hooks_path, "w") as f:
            json.dump(settings, f, indent=2)
    return removed


def cmd_hook_uninstall(args):
    """Remove all Mengram hooks from Claude Code (and Codex, if installed there)"""
    settings_path = get_claude_code_settings_path()

    codex_removed = _uninstall_codex_hooks()
    if codex_removed:
        print(f"Mengram hooks removed from Codex ({get_codex_hooks_path()}).")
    cursor_removed = _uninstall_cursor_hooks()
    if cursor_removed:
        print(f"Mengram hooks removed from Cursor ({get_cursor_hooks_path()}).")
    codex_removed = codex_removed or cursor_removed

    if not settings_path.exists():
        if not codex_removed:
            print("No Claude Code settings found. Nothing to uninstall.")
        return

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except Exception:
        print("Could not read settings file.")
        return

    # Remove every mengram hook
    removed = False
    removed |= _remove_hook(settings, "Stop", "mengram auto-save")
    removed |= _remove_hook(settings, "UserPromptSubmit", "mengram auto-recall")
    removed |= _remove_hook(settings, "SessionStart", "mengram auto-context")
    removed |= _remove_hook(settings, "PreToolUse", "mengram auto-policy")
    removed |= _remove_hook(settings, "PostToolUse", "mengram auto-outcome")
    removed |= _remove_hook(settings, "PreCompact", "mengram auto-checkpoint")

    if not removed:
        if not codex_removed:
            print("No Mengram hooks found. Nothing to uninstall.")
        return

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    # Clean up counter files
    import tempfile, glob as glob_mod
    for f in glob_mod.glob(str(Path(tempfile.gettempdir()) / "mengram-hook-*.count")):
        try:
            os.remove(f)
        except Exception:
            pass

    print("All Mengram hooks removed.")
    print("Restart Claude Code for the change to take effect.")


def cmd_hook_status(args):
    """Check Claude Code hook status"""
    print("Mengram Hooks\n")

    settings_path = get_claude_code_settings_path()
    settings = {}
    if settings_path.exists():
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except Exception:
            pass

    def _find_hook(event_name, marker):
        for group in settings.get("hooks", {}).get(event_name, []):
            for hook in group.get("hooks", []):
                if marker in hook.get("command", ""):
                    return hook.get("command", "")
        return None

    # Check every hook
    save_cmd = _find_hook("Stop", "mengram auto-save")
    recall_cmd = _find_hook("UserPromptSubmit", "mengram auto-recall")
    context_cmd = _find_hook("SessionStart", "mengram auto-context")
    policy_cmd = _find_hook("PreToolUse", "mengram auto-policy")
    outcome_cmd = _find_hook("PostToolUse", "mengram auto-outcome")
    checkpoint_cmd = _find_hook("PreCompact", "mengram auto-checkpoint")

    if save_cmd:
        every_n = 3
        parts = save_cmd.split()
        for i, p in enumerate(parts):
            if p == "--every" and i + 1 < len(parts):
                try: every_n = int(parts[i + 1])
                except ValueError: pass
        print(f"  Auto-save:      installed (every {every_n} responses)")
    else:
        print("  Auto-save:      not installed")

    print(f"  Auto-recall:    {'installed' if recall_cmd else 'not installed'}")
    print(f"  Session context: {'installed' if context_cmd else 'not installed'}")
    print(f"  Policy gate:    {'installed' if policy_cmd else 'not installed'}")
    print(f"  Run outcomes:   {'installed' if outcome_cmd else 'not installed'}")
    print(f"  Checkpoint:     {'installed' if checkpoint_cmd else 'not installed'}")
    codex_path = get_codex_hooks_path()
    if codex_path.exists() and "mengram auto-checkpoint" in codex_path.read_text(errors="ignore"):
        print(f"  Codex:          installed ({codex_path})")
    cursor_path = get_cursor_hooks_path()
    if cursor_path.exists() and "mengram auto-checkpoint" in cursor_path.read_text(errors="ignore"):
        print(f"  Cursor:         installed ({cursor_path})")

    # Installed is not the same as working: run what Claude Code would run.
    broken = []
    for label, cmd in (("Auto-save", save_cmd), ("Auto-recall", recall_cmd),
                       ("Session context", context_cmd), ("Policy gate", policy_cmd),
                       ("Run outcomes", outcome_cmd), ("Checkpoint", checkpoint_cmd)):
        if not cmd:
            continue
        ok, detail = _hook_command_runs(cmd)
        if not ok:
            broken.append((label, cmd, detail))
    if broken:
        print("\n  THESE HOOKS ARE INSTALLED BUT CANNOT RUN:")
        for label, cmd, detail in broken:
            print(f"    {label}: {detail}")
            print(f"      command: {cmd}")
        print("  Claude Code runs hooks in a plain shell that does not read your")
        print("  profile, so a command that works in your terminal can still fail")
        print("  here. Run `mengram hook install` again to write the full path.")
    elif save_cmd or recall_cmd or context_cmd or policy_cmd or outcome_cmd:
        print("  Hook commands:  verified, they run from a plain shell")

    # Check API key — env or ~/.mengram/config.json, the same way the hooks read it.
    # Reading only the env here said "not set" on a working install for months.
    api_key = _load_cloud_api_key()
    if api_key:
        masked = api_key[:6] + "..." + api_key[-4:]
        where = "env" if os.environ.get("MENGRAM_API_KEY") else "~/.mengram/config.json"
        print(f"  API Key:        {masked} (set, {where})")
    else:
        print("  API Key:        not set (MENGRAM_API_KEY or ~/.mengram/config.json)")

    # Check API connectivity
    if api_key:
        try:
            from cloud.client import CloudMemory
            base_url = os.environ.get("MENGRAM_URL", "https://mengram.io")
            mem = CloudMemory(api_key=api_key, base_url=base_url)
            info = mem._request("GET", "/v1/me")
            plan = info.get("plan", "?")
            print(f"  API:            connected ({plan} plan)")
        except Exception as e:
            print(f"  API:            error ({e})")
    else:
        print("  API:            skipped (no key)")

    print(f"  Settings:       {settings_path}")

    any_installed = save_cmd or recall_cmd or context_cmd
    if not any_installed:
        print("\nRun 'mengram hook install' to enable memory hooks.")


def cmd_api(args):
    """Start REST API server"""
    config_path = args.config or str(DEFAULT_CONFIG)

    if not Path(config_path).exists():
        print(f"❌ Run: mengram init")
        sys.exit(1)

    try:
        import fastapi
        import uvicorn
    except ImportError:
        print("❌ FastAPI not installed: pip install mengram[api]")
        sys.exit(1)

    from engine.brain import create_brain
    from api.rest_server import create_rest_api

    brain = create_brain(config_path)

    # Warmup vector store
    if brain.use_vectors:
        _ = brain.vector_store

    app = create_rest_api(brain)

    print(f"🧠 Mengram REST API")
    print(f"   http://localhost:{args.port}")
    print(f"   Docs: http://localhost:{args.port}/docs")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


def cmd_try(args):
    """Local, zero-account preview of what Mengram memory would know."""
    from importer import analyze_claude_code_sessions

    print("🧠 Scanning your local Claude Code history (nothing leaves your machine)...\n")
    report = analyze_claude_code_sessions()
    if not report:
        print("No Claude Code sessions found in ~/.claude/projects/")
        print("Use Claude Code for a few sessions, then run `mengram try` again —")
        print("or start fresh: mengram setup  (free, 30 seconds)")
        return

    span = ""
    if report["first_date"] and report["last_date"]:
        span = f" ({report['first_date']} → {report['last_date']})"
    n_projects = len(report["projects"])
    print(f"Scanned {report['sessions']} sessions across {n_projects} projects{span}.\n")
    print("If this were memory, your AI would already know:\n")

    proj_str = ", ".join(f"{name} ({n})" for name, n in report["projects"][:4])
    print(f"  Projects:   {proj_str}")
    if report["tech"]:
        print(f"  Your stack: {', '.join(report['tech'])}")
    if report["patterns"]:
        print("  Workflow patterns detected:")
        for name, count in report["patterns"]:
            print(f"    ⚙ {name}   (seen in {count} session{'s' if count != 1 else ''})")
    print("\nRight now, every new session starts from zero and relearns all of this.\n")
    print("→ Make it permanent:  mengram setup            (free, 30 seconds)")
    print("→ Then feed it in:    mengram import claude-code")


def cmd_export(args):
    """Write the memory out as files the user owns.

    The server does the serialising and hands back a zip, so the CLI, the API
    and the plugin all produce identical trees — there is no second
    implementation here to drift out of step.
    """
    import io
    import zipfile
    from pathlib import Path

    export_type = getattr(args, "export_type", None)
    if export_type not in ("obsidian", "markdown"):
        print("Usage: mengram export obsidian <vault-path>")
        print("       mengram export markdown <dir>")
        sys.exit(1)

    api_key = _load_cloud_api_key()
    if not api_key:
        print("❌ No API key found (checked MENGRAM_API_KEY env and ~/.mengram/config.json)")
        print("   Run: mengram setup")
        sys.exit(1)

    destination = Path(args.path).expanduser()
    if export_type == "obsidian" and not destination.is_dir():
        print(f"❌ Not a directory: {destination}")
        print("   Point this at your Obsidian vault — the folder holding .obsidian/")
        sys.exit(1)

    # The folder name comes from the format, not from a literal here: the
    # guard below must protect the same directory the archive actually writes
    # into, and those two silently disagreeing is how you overwrite a vault.
    from cloud.markdown_export import ROOT as EXPORT_ROOT
    target = destination / EXPORT_ROOT
    if target.exists() and not args.force:
        print(f"❌ {target} already exists.")
        print("   Re-run with --force to replace it. Anything you wrote inside will be lost.")
        sys.exit(1)

    from cloud.client import CloudMemory
    mem = CloudMemory(api_key=api_key, base_url=_load_cloud_base_url())

    print(f"📦 Exporting memory to {target} …")
    try:
        payload = mem.export(format="markdown", user_id=args.user_id)
    except Exception as e:
        print(f"❌ Export failed: {e}")
        sys.exit(1)

    written = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for name in archive.namelist():
            # Every path comes from our own serialiser, but a zip is still
            # untrusted input: refuse anything that would escape the target.
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                print(f"   ⚠️  skipped suspicious path: {name}")
                continue
            out = destination / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(archive.read(name))
            written += 1

    print(f"✅ {written} files written to {target}")
    if export_type == "obsidian":
        print("   Open the vault — relations are wikilinks, so the graph view just works.")
    print("   These are yours. Plain Markdown, no account needed to read them.")


def cmd_import(args):
    """Import existing data into memory"""
    import_type = args.import_type
    if not import_type:
        print("Usage: mengram import {claude-code,chatgpt,obsidian,files} <path>")
        print("  mengram import claude-code            # your local Claude Code sessions")
        print("  mengram import claude-code --memory ./memory   # ...into a local memory folder, no account")
        print("  mengram import chatgpt ~/Downloads/chatgpt-export.zip")
        print("  mengram import obsidian ~/Documents/MyVault")
        print("  mengram import files notes/*.md")
        sys.exit(1)

    # --- Claude Code local transcripts: cloud-first, self-contained flow ---
    if import_type == "claude-code":
        from importer import import_claude_code, discover_claude_code_sessions, RateLimiter

        local_dir = _local_dir(args)
        if local_dir:
            sys.exit(_local_import_claude_code(args, local_dir))

        api_key = _load_cloud_api_key()
        if not api_key:
            print("❌ No API key found. Run `mengram setup` (or save it to ~/.mengram/config.json)")
            sys.exit(1)

        available = discover_claude_code_sessions(getattr(args, "project", "") or "")
        if not available:
            print("❌ No Claude Code sessions found in ~/.claude/projects/")
            sys.exit(1)

        n = min(getattr(args, "last", 20), len(available))
        print(f"🧠 Found {len(available)} Claude Code sessions; importing up to {n} most recent.")
        print("   Each session = 1 add operation (counts against your plan's monthly add quota).")
        if not getattr(args, "yes", False):
            answer = input("   Continue? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("Aborted.")
                sys.exit(0)

        from cloud.client import CloudMemory
        mem = CloudMemory(api_key=api_key, base_url=_load_cloud_base_url())
        limiter = RateLimiter(max_per_minute=30)
        user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")

        def cc_add_fn(text, session_id):
            limiter.wait_if_needed()
            return mem.add_text(text, user_id=user_id, source="claude_code_import",
                                run_id=session_id)

        def cc_progress(current, total, title):
            pct = int(current / total * 100) if total else 0
            bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
            print(f"\r  {bar} {pct}% ({current}/{total}) {title}", end="", flush=True)

        print()
        result = import_claude_code(
            cc_add_fn,
            last=getattr(args, "last", 20),
            project_filter=getattr(args, "project", "") or "",
            reimport=getattr(args, "reimport", False),
            on_progress=cc_progress,
        )

        print(f"\n\n{'='*50}")
        print(f"✅ Import complete!\n")
        print(f"   Sessions considered: {result.conversations_found}")
        print(f"   Imported:            {result.chunks_sent}")
        print(f"   Time:                {result.duration_seconds:.1f}s")
        if result.errors:
            print(f"\n   ⚠️  {len(result.errors)} errors:")
            for err in result.errors[:5]:
                print(f"      - {err}")

        # The wow moment: show what memory actually LEARNED — especially
        # procedural workflows, which no session-persistence tool extracts.
        if result.chunks_sent > 0:
            import time as _t
            print("\n   ⏳ Extracting memories (facts, events, workflows)...", flush=True)
            baseline = 0
            try:
                baseline_stats = mem.stats(user_id=user_id) if hasattr(mem, "stats") else {}
                baseline = baseline_stats.get("facts", 0)
            except Exception:
                pass
            learned = None
            for _ in range(6):
                _t.sleep(10)
                try:
                    s = mem.stats(user_id=user_id) if hasattr(mem, "stats") else {}
                    if s.get("facts", 0) > baseline or s.get("procedures", 0) > 0:
                        learned = s
                        break
                except Exception:
                    break
            if learned:
                print(f"\n   🧠 Memory now holds: {learned.get('entities', 0)} entities, "
                      f"{learned.get('facts', 0)} facts, {learned.get('episodes', 0)} episodes, "
                      f"{learned.get('procedures', 0)} workflows")
                try:
                    procs = mem.procedures(limit=3, user_id=user_id)
                    if procs:
                        print("\n   Learned workflows (these evolve as you succeed or fail):")
                        for p in procs[:3]:
                            print(f"      ⚙ {p.get('name', '?')} — {len(p.get('steps', []))} steps")
                except Exception:
                    pass
            else:
                print("   No new memories surfaced yet — either these sessions were already")
                print("   in memory (extraction dedupes), or processing needs another minute.")

        print("\n   Try asking Claude Code: \"what do you know about my projects?\"")
        print("   Dashboard: https://mengram.io/dashboard")
        print("   Already-imported sessions are skipped on re-runs (use --reimport to force).")
        return

    from importer import (
        import_chatgpt, import_obsidian, import_files, RateLimiter,
    )

    # --- Resolve add_fn ---
    if getattr(args, "cloud", False):
        api_key = os.environ.get("MENGRAM_API_KEY", "")
        if not api_key:
            print("❌ Set MENGRAM_API_KEY environment variable")
            sys.exit(1)

        from cloud.client import CloudMemory
        mem = CloudMemory(api_key=api_key)
        limiter = RateLimiter(max_per_minute=100)

        def add_fn(messages):
            limiter.wait_if_needed()
            return mem.add(messages)

        print("☁️  Importing to cloud memory...")
    else:
        config_path = str(DEFAULT_CONFIG)
        if not Path(config_path).exists():
            print("❌ Run: mengram init  (or use --cloud for cloud API)")
            sys.exit(1)

        from engine.brain import create_brain
        brain = create_brain(config_path)
        add_fn = brain.remember
        print("💾 Importing to local memory...")

    # --- Progress callback ---
    def on_progress(current, total, title):
        pct = int(current / total * 100) if total else 0
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"\r  {bar} {pct}% ({current}/{total}) {title[:40]}", end="", flush=True)

    # --- Run importer ---
    print()
    if import_type == "chatgpt":
        result = import_chatgpt(args.path, add_fn,
                                chunk_size=args.chunk_size, on_progress=on_progress)
    elif import_type == "obsidian":
        result = import_obsidian(args.path, add_fn,
                                 chunk_chars=args.chunk_chars, on_progress=on_progress)
    elif import_type == "files":
        result = import_files(args.paths, add_fn,
                              chunk_chars=args.chunk_chars, on_progress=on_progress)
    else:
        print(f"❌ Unknown import type: {import_type}")
        sys.exit(1)

    # --- Summary ---
    print(f"\n\n{'='*50}")
    print(f"✅ Import complete!\n")
    print(f"   Found:    {result.conversations_found} {'conversations' if import_type == 'chatgpt' else 'files'}")
    print(f"   Imported: {result.chunks_sent} chunks")
    print(f"   Entities: {len(result.entities_created)}")
    print(f"   Time:     {result.duration_seconds:.1f}s")
    if result.errors:
        print(f"\n   ⚠️  {len(result.errors)} errors:")
        for err in result.errors[:5]:
            print(f"      - {err}")
        if len(result.errors) > 5:
            print(f"      ... and {len(result.errors) - 5} more")


def cmd_web(args):
    """Start Web UI — chat + knowledge graph"""
    config_path = args.config or str(DEFAULT_CONFIG)

    if not Path(config_path).exists():
        print(f"❌ Run: mengram init")
        sys.exit(1)

    try:
        import fastapi
        import uvicorn
    except ImportError:
        print("❌ FastAPI not installed: pip install mengram[api]")
        sys.exit(1)

    from engine.brain import create_brain
    from api.rest_server import create_rest_api

    brain = create_brain(config_path)

    if brain.use_vectors:
        _ = brain.vector_store

    app = create_rest_api(brain)

    url = f"http://localhost:{args.port}"
    print(f"🧠 Mengram Web UI")
    print(f"   {url}")
    print(f"   API docs: {url}/docs")
    print()

    if not args.no_open:
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


def main():
    parser = argparse.ArgumentParser(
        prog="mengram",
        description="🧠 Mengram — AI memory layer for apps",
    )
    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="Setup Mengram")
    p_init.add_argument("--provider", choices=["anthropic", "openai", "ollama"], help="LLM provider")
    p_init.add_argument("--api-key", help="API key")
    p_init.add_argument("--vault", help="Custom vault path")
    p_init.add_argument("--home", help="Mengram home dir (default: ~/.mengram)")
    p_init.add_argument("--no-mcp", action="store_true", help="Skip Claude Desktop MCP setup")
    p_init.add_argument("--mcp-only", action="store_true", help="Only setup MCP (config must exist)")

    # server
    p_server = sub.add_parser("server", help="Start MCP server")
    p_server.add_argument("--config", help="Config path (default: ~/.mengram/config.yaml)")
    p_server.add_argument("--cloud", action="store_true", help="Use cloud API instead of local vault")
    p_server.add_argument("--memory", default=None, help="Local mode: serve a memory folder (no account)")

    # status
    sub.add_parser("status", help="Check setup status")

    # receipt
    p_resume = sub.add_parser("resume", help="Where the task in this repository stands (card written at Stop)")
    p_resume.add_argument("--open", action="store_true", help="Open the local page to confirm or correct the card")
    p_resume.add_argument("--json", action="store_true", help="Print the card as JSON")
    p_resume.add_argument("--any-age", action="store_true", dest="any_age", help="Show the card however old")
    p_resume.add_argument("--path", help="Repository path (default: current directory)")

    p_receipt = sub.add_parser("receipt", help="What memory did: last session and the past days")
    p_receipt.add_argument("--days", type=int, default=7, help="Window for the totals (default 7)")

    # stats
    p_stats = sub.add_parser("stats", help="Vault statistics")
    p_stats.add_argument("--config", help="Config path")

    # rules
    p_rules = sub.add_parser("rules", help="Generate CLAUDE.md / .cursorrules from cloud memory")
    p_rules.add_argument("--format", choices=["claude_md", "cursorrules", "windsurf"],
                          default="claude_md", help="Output format (default: claude_md)")
    p_rules.add_argument("--force", action="store_true", help="Regenerate (bypass cache)")

    # api
    p_api = sub.add_parser("api", help="Start REST API server")
    p_api.add_argument("--config", help="Config path")
    p_api.add_argument("--host", default="0.0.0.0", help="Host (default: 0.0.0.0)")
    p_api.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")

    # try — zero-account local preview
    sub.add_parser("try", help="Preview what Mengram memory would know — local only, no account needed")

    # export
    p_export = sub.add_parser("export", help="Write your memory out as plain Markdown files")
    export_sub = p_export.add_subparsers(dest="export_type")

    p_exp_obs = export_sub.add_parser(
        "obsidian", help="Write a memory/ folder into an Obsidian vault")
    p_exp_obs.add_argument("path", help="Path to the vault directory")
    p_exp_obs.add_argument("--user-id", default="default", dest="user_id",
                           help="Export one sub-user's memory")
    p_exp_obs.add_argument("--force", action="store_true",
                           help="Overwrite an existing memory/ folder")

    p_exp_md = export_sub.add_parser(
        "markdown", help="Write the same tree into any directory")
    p_exp_md.add_argument("path", help="Destination directory")
    p_exp_md.add_argument("--user-id", default="default", dest="user_id")
    p_exp_md.add_argument("--force", action="store_true")

    # import
    p_import = sub.add_parser("import", help="Import existing data into memory")
    import_sub = p_import.add_subparsers(dest="import_type")

    p_cc = import_sub.add_parser("claude-code", help="Import your local Claude Code sessions (~/.claude/projects)")
    p_cc.add_argument("--last", type=int, default=20, help="How many most-recent sessions to import (default 20)")
    p_cc.add_argument("--project", default="", help="Only sessions whose project path contains this substring")
    p_cc.add_argument("--reimport", action="store_true", help="Re-import sessions that were already imported")
    p_cc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    p_cc.add_argument("--user-id", default=None, dest="user_id")
    p_cc.add_argument("--memory", default=None, metavar="DIR",
                      help="Import into a local memory folder instead of the cloud (or set MENGRAM_MEMORY_DIR)")

    p_chatgpt = import_sub.add_parser("chatgpt", help="Import ChatGPT export ZIP")
    p_chatgpt.add_argument("path", help="Path to ChatGPT export ZIP file")
    p_chatgpt.add_argument("--chunk-size", type=int, default=20, dest="chunk_size")
    p_chatgpt.add_argument("--cloud", action="store_true", help="Use cloud API")

    p_obsidian = import_sub.add_parser("obsidian", help="Import Obsidian vault")
    p_obsidian.add_argument("path", help="Path to Obsidian vault directory")
    p_obsidian.add_argument("--chunk-chars", type=int, default=4000, dest="chunk_chars")
    p_obsidian.add_argument("--cloud", action="store_true", help="Use cloud API")

    p_files = import_sub.add_parser("files", help="Import text/markdown files")
    p_files.add_argument("paths", nargs="+", help="File paths")
    p_files.add_argument("--chunk-chars", type=int, default=4000, dest="chunk_chars")
    p_files.add_argument("--cloud", action="store_true", help="Use cloud API")

    # hook
    p_hook = sub.add_parser("hook", help="Manage Claude Code auto-save hook")
    hook_sub = p_hook.add_subparsers(dest="hook_action")
    p_hook_install = hook_sub.add_parser("install", help="Install auto-save hook")
    p_hook_install.add_argument("--every", type=int, default=3,
                                 help="Save every Nth response (default: 3)")
    p_hook_install.add_argument("--user-id", default=None,
                                 help="Mengram user_id (default: 'default')")
    p_hook_install.add_argument("--no-policy", action="store_true", dest="no_policy",
                                 help="Skip the PreToolUse policy gate")
    p_hook_install.add_argument("--memory", default=None,
                                 help="Local mode: memory folder (no account needed)")
    p_hook_install.add_argument("--codex", action="store_true",
                                 help="Install into Codex (~/.codex/hooks.json) instead of Claude Code")
    p_hook_install.add_argument("--cursor", action="store_true",
                                 help="Install into Cursor (~/.cursor/hooks.json) instead of Claude Code")
    hook_sub.add_parser("uninstall", help="Remove auto-save hook")
    hook_sub.add_parser("status", help="Check hook status")

    # auto-save (internal, called by Claude Code Stop hook)
    p_autosave = sub.add_parser("auto-save", help=argparse.SUPPRESS)
    p_autosave.add_argument("--every", type=int, default=3)
    p_autosave.add_argument("--user-id", default=None)
    p_autosave.add_argument("--memory", default=None)
    p_autosave.add_argument("--verbose", action="store_true",
                             help="Emit a status marker for each hook invocation")
    p_autosave.add_argument("--host", default=None,
                             help="Which agent fired the hook (claude-code, codex, cursor)")

    # auto-recall (internal, called by Claude Code UserPromptSubmit hook)
    p_autorecall = sub.add_parser("auto-recall", help=argparse.SUPPRESS)
    p_autorecall.add_argument("--user-id", default=None)
    p_autorecall.add_argument("--memory", default=None)
    p_autorecall.add_argument("--verbose", action="store_true",
                               help="Emit a status marker for each hook invocation")

    # auto-context (internal, called by Claude Code SessionStart hook)
    p_autocontext = sub.add_parser("auto-context", help=argparse.SUPPRESS)
    p_autocontext.add_argument("--user-id", default=None)
    p_autocontext.add_argument("--memory", default=None)
    p_autocontext.add_argument("--verbose", action="store_true",
                                help="Emit a status marker for each hook invocation")
    p_autocontext.add_argument("--no-weekly", action="store_true",
                                help="Suppress the once-a-week memory report")
    p_autocontext.add_argument("--host", default=None,
                                help="Which agent fired the hook (claude-code, codex, cursor)")

    # auto-policy (internal, called by Claude Code PreToolUse hook on Bash)
    p_autopolicy = sub.add_parser("auto-policy", help=argparse.SUPPRESS)
    p_autopolicy.add_argument("--user-id", default=None)
    p_autopolicy.add_argument("--memory", default=None)
    p_autopolicy.add_argument("--verbose", action="store_true",
                               help="Emit a status marker for each hook invocation")
    p_autopolicy.add_argument("--min-reliable", type=int, default=None, dest="min_reliable",
                               help="Percent below which a workflow with a record is confirmed (default 70)")

    # auto-outcome (internal, called by Claude Code PostToolUse hook on Bash)
    p_autooutcome = sub.add_parser("auto-outcome", help=argparse.SUPPRESS)
    p_autooutcome.add_argument("--user-id", default=None)
    p_autooutcome.add_argument("--memory", default=None)
    p_autooutcome.add_argument("--verbose", action="store_true",
                                help="Emit a status marker for each hook invocation")

    # auto-checkpoint (internal, called on PreCompact by Claude Code and Codex)
    p_autocheckpoint = sub.add_parser("auto-checkpoint", help=argparse.SUPPRESS)
    p_autocheckpoint.add_argument("--host", default=None,
                                   help="Which agent fired the hook (claude-code, codex)")
    p_autocheckpoint.add_argument("--verbose", action="store_true",
                                   help="Emit a status marker for each hook invocation")

    # auto-restore (internal, Cursor postToolUse: checkpoint back after compaction)
    p_autorestore = sub.add_parser("auto-restore", help=argparse.SUPPRESS)
    p_autorestore.add_argument("--host", default=None)
    p_autorestore.add_argument("--verbose", action="store_true")

    # local — memory in a folder, no account
    from local.cli import add_parser as _add_local_parser
    _add_local_parser(sub)

    # web
    p_web = sub.add_parser("web", help="Start Web UI (chat + knowledge graph)")
    p_web.add_argument("--config", help="Config path")
    p_web.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    p_web.add_argument("--no-open", action="store_true", help="Don't open browser")

    # setup (interactive signup + hook install)
    p_weekly = sub.add_parser("weekly", help="Weekly memory report (facts, procedures, prevented repeats)")
    p_weekly.add_argument("--share", action="store_true", help="Also print + copy a ready-to-post summary")
    p_weekly.add_argument("--no-color", action="store_true", help="Plain output (for piping)")
    p_weekly.add_argument("--user-id", default=None, dest="user_id")

    p_setup = sub.add_parser("setup", help="Sign up and configure Mengram (interactive)")
    p_setup.add_argument("--email", help="Email (skip prompt)")
    p_setup.add_argument("--key", help="API key (skip signup, just save key + install hooks)")
    p_setup.add_argument("--no-hooks", action="store_true", help="Skip Claude Code hook install")
    p_setup.add_argument("--no-tools", action="store_true", help="Skip Cursor/Claude Desktop/Windsurf MCP config")
    p_setup.add_argument("--no-import", action="store_true", help="Skip Claude Code history import")
    p_setup.add_argument("--no-verify", action="store_true", help="Skip round-trip verification")

    # signup (non-interactive — designed for agent-driven installs)
    p_signup = sub.add_parser(
        "signup",
        help="Non-interactive signup. Without --code, sends verification email. "
             "With --code, completes signup and saves API key.",
    )
    p_signup.add_argument("--email", required=True, help="Account email")
    p_signup.add_argument("--code", help="6-digit verification code from your inbox")

    # doctor (round-trip cloud API test)
    sub.add_parser(
        "doctor",
        help="Verify the cloud install works end-to-end (add + search round-trip).",
    )

    args = parser.parse_args()

    if args.command == "init":
        cmd_init(args)
    elif args.command == "server":
        cmd_server(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "receipt":
        cmd_receipt(args)
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "rules":
        cmd_rules(args)
    elif args.command == "api":
        cmd_api(args)
    elif args.command == "try":
        cmd_try(args)
    elif args.command == "export":
        cmd_export(args)
    elif args.command == "import":
        cmd_import(args)
    elif args.command == "hook":
        cmd_hook(args)
    elif args.command == "auto-save":
        cmd_auto_save(args)
    elif args.command == "auto-recall":
        cmd_auto_recall(args)
    elif args.command == "auto-context":
        cmd_auto_context(args)
    elif args.command == "auto-policy":
        cmd_auto_policy(args)
    elif args.command == "auto-outcome":
        cmd_auto_outcome(args)
    elif args.command == "auto-checkpoint":
        cmd_auto_checkpoint(args)
    elif args.command == "auto-restore":
        cmd_auto_restore(args)
    elif args.command == "local":
        from local.cli import run as _run_local
        sys.exit(_run_local(args))
    elif args.command == "web":
        cmd_web(args)
    elif args.command == "weekly":
        cmd_weekly(args)
    elif args.command == "setup":
        cmd_setup(args)
    elif args.command == "signup":
        cmd_signup(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
