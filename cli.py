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


def cmd_auto_recall(args):
    """Hook handler — called by Claude Code on UserPromptSubmit. Searches Mengram for relevant context."""
    HOOK = "auto-recall"
    EVENT = "UserPromptSubmit"
    try:
        api_key = _load_cloud_api_key()
        if not api_key:
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

        from cloud.client import CloudMemory
        base_url = _load_cloud_base_url()
        user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")

        mem = CloudMemory(api_key=api_key, base_url=base_url)
        results = mem.search(prompt, user_id=user_id, limit=3, graph_depth=1)

        if not results:
            _emit_hook_exit(EVENT, args, HOOK, "no memories found")

        # Format context
        lines = ["[Mengram Memory — relevant context from past sessions]"]
        for r in results:
            entity = r.get("entity", "")
            facts = r.get("facts", [])
            if entity and facts:
                lines.append(f"\n{entity}:")
                for fact in facts[:5]:
                    lines.append(f"  - {fact}")

        context = "\n".join(lines)
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


def cmd_auto_context(args):
    """Hook handler — called by Claude Code on SessionStart. Loads cognitive profile as context."""
    HOOK = "auto-context"
    EVENT = "SessionStart"
    try:
        api_key = _load_cloud_api_key()
        if not api_key:
            _emit_hook_exit(EVENT, args, HOOK, "no API key")

        from cloud.client import CloudMemory
        base_url = _load_cloud_base_url()
        user_id = getattr(args, "user_id", None) or os.environ.get("MENGRAM_USER_ID", "default")

        mem = CloudMemory(api_key=api_key, base_url=base_url)
        profile = mem.get_profile(user_id=user_id)

        system_prompt = profile.get("system_prompt", "")
        if not system_prompt:
            _emit_hook_exit(EVENT, args, HOOK, "no profile")

        context = f"[Mengram Memory — user context loaded from past sessions]\n{system_prompt}"
        _emit_hook_exit(EVENT, args, HOOK, f"context loaded ({len(system_prompt)} chars)", context=context)

    except SystemExit:
        raise
    except Exception as e:
        if _is_quota_error(e):
            _emit_hook_exit(
                EVENT, args, HOOK, "quota exceeded",
                context=(
                    f"[Mengram] Memory profile load failed — quota exceeded. {e} "
                    "Upgrade at https://mengram.io/dashboard"
                ),
            )
        _emit_hook_exit(EVENT, args, HOOK, "error")


def cmd_auto_save(args):
    """Hook handler — called by Claude Code on Stop event. Reads stdin, saves to Mengram."""
    HOOK = "auto-save"
    EVENT = "Stop"
    try:
        api_key = _load_cloud_api_key()
        if not api_key:
            _emit_hook_exit(EVENT, args, HOOK, "no API key")

        # Read hook input from stdin
        try:
            input_data = json.loads(sys.stdin.read())
        except Exception:
            _emit_hook_exit(EVENT, args, HOOK, "no input")

        # Avoid infinite loops
        if input_data.get("stop_hook_active"):
            _emit_hook_exit(EVENT, args, HOOK, "skipped (stop_hook_active)")

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
        if transcript_path and Path(transcript_path).exists():
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
        )

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


def _upsert_hook(settings, event_name, command_marker, hook_def):
    """Insert or update a hook in settings[hooks][event_name] matching command_marker."""
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
        settings["hooks"][event_name].append({"hooks": [hook_def]})

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


def _emit_hook_exit(hook_event_name, args, hook_name, status, context=None):
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
    if not no_hooks:
        try:
            cmd_hook_install(args)
        except SystemExit:
            pass
    else:
        print("\n  Skipped hook install (--no-hooks).")

    print("\n  Done! Restart Claude Code — it now remembers everything.\n")


def cmd_hook_install(args):
    """Install Claude Code memory hooks (auto-save + auto-recall + session context)"""
    api_key = os.environ.get("MENGRAM_API_KEY", "")
    if not api_key:
        print("Set MENGRAM_API_KEY environment variable first", file=sys.stderr)
        print("Run 'mengram setup' to create an account and configure automatically", file=sys.stderr)
        print("Or get a key at: https://mengram.io/#signup", file=sys.stderr)
        sys.exit(1)

    every = getattr(args, "every", 3) or 3
    user_id = getattr(args, "user_id", None)

    # Build hook commands
    save_cmd = f"mengram auto-save --every {every}"
    recall_cmd = "mengram auto-recall"
    context_cmd = "mengram auto-context"
    if user_id:
        save_cmd += f" --user-id {user_id}"
        recall_cmd += f" --user-id {user_id}"
        context_cmd += f" --user-id {user_id}"

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

    # Write settings
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)

    print("Mengram hooks installed:")
    print(f"  Auto-save:    every {every} response(s) (background)")
    print(f"  Auto-recall:  search memory on each prompt")
    print(f"  Session context: load profile on session start")
    print(f"  Settings: {settings_path}")
    print(f"\nRestart Claude Code for hooks to take effect.")


def cmd_hook_uninstall(args):
    """Remove all Mengram hooks from Claude Code"""
    settings_path = get_claude_code_settings_path()

    if not settings_path.exists():
        print("No Claude Code settings found. Nothing to uninstall.")
        return

    try:
        with open(settings_path) as f:
            settings = json.load(f)
    except Exception:
        print("Could not read settings file.")
        return

    # Remove all 3 mengram hooks
    removed = False
    removed |= _remove_hook(settings, "Stop", "mengram auto-save")
    removed |= _remove_hook(settings, "UserPromptSubmit", "mengram auto-recall")
    removed |= _remove_hook(settings, "SessionStart", "mengram auto-context")

    if not removed:
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

    # Check all 3 hooks
    save_cmd = _find_hook("Stop", "mengram auto-save")
    recall_cmd = _find_hook("UserPromptSubmit", "mengram auto-recall")
    context_cmd = _find_hook("SessionStart", "mengram auto-context")

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

    # Check API key
    api_key = os.environ.get("MENGRAM_API_KEY", "")
    if api_key:
        masked = api_key[:6] + "..." + api_key[-4:]
        print(f"  API Key:        {masked} (set)")
    else:
        print("  API Key:        not set")

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


def cmd_import(args):
    """Import existing data into memory"""
    import_type = args.import_type
    if not import_type:
        print("Usage: mengram import {claude-code,chatgpt,obsidian,files} <path>")
        print("  mengram import claude-code            # your local Claude Code sessions")
        print("  mengram import chatgpt ~/Downloads/chatgpt-export.zip")
        print("  mengram import obsidian ~/Documents/MyVault")
        print("  mengram import files notes/*.md")
        sys.exit(1)

    # --- Claude Code local transcripts: cloud-first, self-contained flow ---
    if import_type == "claude-code":
        from importer import import_claude_code, discover_claude_code_sessions, RateLimiter

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

    # status
    sub.add_parser("status", help="Check setup status")

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

    # import
    p_import = sub.add_parser("import", help="Import existing data into memory")
    import_sub = p_import.add_subparsers(dest="import_type")

    p_cc = import_sub.add_parser("claude-code", help="Import your local Claude Code sessions (~/.claude/projects)")
    p_cc.add_argument("--last", type=int, default=20, help="How many most-recent sessions to import (default 20)")
    p_cc.add_argument("--project", default="", help="Only sessions whose project path contains this substring")
    p_cc.add_argument("--reimport", action="store_true", help="Re-import sessions that were already imported")
    p_cc.add_argument("--yes", action="store_true", help="Skip the confirmation prompt")
    p_cc.add_argument("--user-id", default=None, dest="user_id")

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
    hook_sub.add_parser("uninstall", help="Remove auto-save hook")
    hook_sub.add_parser("status", help="Check hook status")

    # auto-save (internal, called by Claude Code Stop hook)
    p_autosave = sub.add_parser("auto-save", help=argparse.SUPPRESS)
    p_autosave.add_argument("--every", type=int, default=3)
    p_autosave.add_argument("--user-id", default=None)
    p_autosave.add_argument("--verbose", action="store_true",
                             help="Emit a status marker for each hook invocation")

    # auto-recall (internal, called by Claude Code UserPromptSubmit hook)
    p_autorecall = sub.add_parser("auto-recall", help=argparse.SUPPRESS)
    p_autorecall.add_argument("--user-id", default=None)
    p_autorecall.add_argument("--verbose", action="store_true",
                               help="Emit a status marker for each hook invocation")

    # auto-context (internal, called by Claude Code SessionStart hook)
    p_autocontext = sub.add_parser("auto-context", help=argparse.SUPPRESS)
    p_autocontext.add_argument("--user-id", default=None)
    p_autocontext.add_argument("--verbose", action="store_true",
                                help="Emit a status marker for each hook invocation")

    # web
    p_web = sub.add_parser("web", help="Start Web UI (chat + knowledge graph)")
    p_web.add_argument("--config", help="Config path")
    p_web.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    p_web.add_argument("--no-open", action="store_true", help="Don't open browser")

    # setup (interactive signup + hook install)
    p_setup = sub.add_parser("setup", help="Sign up and configure Mengram (interactive)")
    p_setup.add_argument("--email", help="Email (skip prompt)")
    p_setup.add_argument("--key", help="API key (skip signup, just save key + install hooks)")
    p_setup.add_argument("--no-hooks", action="store_true", help="Skip Claude Code hook install")

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
    elif args.command == "stats":
        cmd_stats(args)
    elif args.command == "rules":
        cmd_rules(args)
    elif args.command == "api":
        cmd_api(args)
    elif args.command == "try":
        cmd_try(args)
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
    elif args.command == "web":
        cmd_web(args)
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
