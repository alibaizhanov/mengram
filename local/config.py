"""Where the folder is, and which model to use.

Two questions, answered in this order:

- the folder: `--memory DIR` on the command, else `MENGRAM_MEMORY_DIR`;
- the model: `DIR/.mengram/config.json` → `{"llm": {...}}`, else whichever
  of ANTHROPIC_API_KEY / OPENAI_API_KEY is set, else Ollama if reachable is
  *not* assumed — the user says so in the config.

No model is a normal state. Search, recall, the policy gate and feedback
work without one; only `add` and a failure revision need it, and they say so.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_REL = Path(".mengram") / "config.json"


def memory_dir(explicit: str | None = None) -> Path | None:
    """The memory folder for local mode, or None when local mode is off."""
    raw = explicit or os.environ.get("MENGRAM_MEMORY_DIR", "").strip()
    return Path(raw).expanduser() if raw else None


def config_path(root: Path) -> Path:
    return Path(root) / CONFIG_REL


def read_config(root: Path) -> dict:
    path = config_path(root)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_config(root: Path, config: dict) -> Path:
    path = config_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def llm_config(root: Path) -> dict | None:
    """The `llm` block to hand `create_llm_client`, or None with no model."""
    cfg = read_config(root).get("llm") or {}
    provider = (cfg.get("provider") or "").strip().lower()
    if provider:
        return cfg
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        return {"provider": "anthropic", "anthropic": {"api_key": key}}
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return {"provider": "openai", "openai": {"api_key": key}}
    return None


def llm_client(root: Path):
    """A client for the user's model, or None. Never raises for "no model"."""
    cfg = llm_config(root)
    if not cfg:
        return None
    if cfg.get("provider") == "mock":
        from engine.extractor.conversation_extractor import MockLLMClient
        return MockLLMClient()
    from engine.extractor.llm_client import create_llm_client
    return create_llm_client(cfg)


def describe_model(root: Path) -> str:
    cfg = llm_config(root)
    if not cfg:
        return "no model configured"
    provider = cfg.get("provider", "?")
    model = (cfg.get(provider) or {}).get("model")
    return f"{provider}" + (f" / {model}" if model else "")
