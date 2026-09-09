#!/usr/bin/env python3
"""Local-model extraction benchmark for Mengram.

Runs the exact extraction prompt Mengram uses (the one we run with claude-sonnet-5
in the cloud and in `mengram import claude-code`) against one or more Ollama
models, and reports JSON validity, latency, and how much each model pulled out.

    pip install mengram-ai
    python bench.py --models llama3.1:8b,qwen2.5:14b,gemma3:12b
    python bench.py --models qwen2.5:14b --format none        # no `format: json`
    python bench.py --models qwen3:8b --think                  # let thinking models think
    python bench.py --transcript my-session.md --runs 3        # your own transcript, 3 runs each
    python bench.py --dump-prompt prompt.txt                   # just write the rendered prompt

Transcript format: a text file with "User:" / "Assistant:" paragraphs (see
sample-transcript.md). Each paragraph that starts with one of those labels is one
message; everything else continues the previous message.

The script talks to Ollama's /api/chat directly, so it does not depend on the
Ollama client shipped in the package. Stdlib only, no extra dependencies.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

try:
    from engine.extractor.conversation_extractor import (
        EXTRACTION_PROMPT,
        EXTRACTION_SCHEMA,
        ConversationExtractor,
    )
except ImportError:  # pragma: no cover
    sys.exit("mengram-ai is not installed: pip install mengram-ai")

HERE = Path(__file__).resolve().parent
DEFAULT_TRANSCRIPT = HERE / "sample-transcript.md"


def parse_transcript(path: Path) -> list[dict]:
    """'User:' / 'Assistant:' paragraphs → [{role, content}]."""
    messages: list[dict] = []
    for block in path.read_text(encoding="utf-8").split("\n\n"):
        stripped = block.strip()
        if not stripped:
            continue
        for label, role in (("User:", "user"), ("Assistant:", "assistant")):
            if stripped.startswith(label):
                messages.append({"role": role, "content": stripped[len(label):].strip()})
                break
        else:
            if messages:
                messages[-1]["content"] += "\n\n" + stripped
            else:
                messages.append({"role": "user", "content": stripped})
    if not messages:
        sys.exit(f"no messages found in {path}")
    return messages


class OllamaChat:
    """Minimal Ollama /api/chat client with the knobs that matter for extraction."""

    def __init__(self, model: str, base_url: str, num_ctx: int, use_format: bool,
                 think: bool, timeout: float):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.use_format = use_format
        self.think = think
        self.timeout = timeout
        self.raw_outputs: list[str] = []

    def complete(self, prompt: str, system: str = "", response_format=None) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": self.think,
            "options": {"num_ctx": self.num_ctx, "temperature": 0.2},
        }
        if response_format is not None and self.use_format:
            body["format"] = "json"
        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        text = data.get("message", {}).get("content", "")
        self.raw_outputs.append(text)
        return text


def is_valid_json(text: str) -> bool:
    try:
        json.loads(text)
        return True
    except (TypeError, ValueError):
        return False


def run_one(client: OllamaChat, conversation: list[dict]) -> dict:
    client.raw_outputs.clear()
    started = time.time()
    error = ""
    try:
        result = ConversationExtractor(client).extract(conversation, existing_context="")
        entities, episodes, procedures = len(result.entities), len(result.episodes), len(result.procedures)
        facts = sum(len(e.facts) for e in result.entities)
    except Exception as exc:  # noqa: BLE001 - we want the model failure, whatever it is
        entities = episodes = procedures = facts = 0
        error = f"{type(exc).__name__}: {str(exc)[:80]}"
    elapsed = time.time() - started
    first = client.raw_outputs[0] if client.raw_outputs else ""
    return {
        "seconds": round(elapsed, 1),
        "calls": len(client.raw_outputs),
        "valid_json_first_try": is_valid_json(first),
        "raw_chars": len(first),
        "entities": entities,
        "facts": facts,
        "episodes": episodes,
        "procedures": procedures,
        "error": error,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", default="llama3.1:8b", help="comma-separated Ollama model tags")
    ap.add_argument("--transcript", type=Path, default=DEFAULT_TRANSCRIPT)
    ap.add_argument("--runs", type=int, default=1, help="runs per model (report median seconds)")
    ap.add_argument("--num-ctx", type=int, default=16384)
    ap.add_argument("--format", choices=["json", "none"], default="json",
                    help="send Ollama `format: json` (default) or not")
    ap.add_argument("--think", action="store_true", help="enable thinking for models that support it")
    ap.add_argument("--base-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--timeout", type=float, default=900.0)
    ap.add_argument("--out", type=Path, default=HERE / "results.json")
    ap.add_argument("--dump-prompt", type=Path, help="write the rendered prompt for the transcript and exit")
    args = ap.parse_args()

    conversation = parse_transcript(args.transcript)
    chars = sum(len(m["content"]) for m in conversation)

    if args.dump_prompt:
        extractor = ConversationExtractor(llm_client=None)
        rendered = EXTRACTION_PROMPT.format(
            conversation=extractor._format_conversation(conversation), existing_context="")
        args.dump_prompt.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.dump_prompt} ({len(rendered)} chars)")
        return

    print(f"transcript: {args.transcript.name}, {len(conversation)} messages, {chars} chars; "
          f"num_ctx={args.num_ctx} format={args.format} think={args.think}", file=sys.stderr)

    rows = []
    for model in [m.strip() for m in args.models.split(",") if m.strip()]:
        client = OllamaChat(model, args.base_url, args.num_ctx, args.format == "json", args.think, args.timeout)
        runs = []
        for i in range(args.runs):
            r = run_one(client, conversation)
            runs.append(r)
            print(json.dumps({"model": model, "run": i + 1, **r}), file=sys.stderr)
        rows.append({
            "model": model,
            "runs": args.runs,
            "seconds_median": statistics.median(r["seconds"] for r in runs),
            "valid_json_first_try": f"{sum(r['valid_json_first_try'] for r in runs)}/{len(runs)}",
            "entities": statistics.median(r["entities"] for r in runs),
            "facts": statistics.median(r["facts"] for r in runs),
            "episodes": statistics.median(r["episodes"] for r in runs),
            "procedures": statistics.median(r["procedures"] for r in runs),
            "errors": [r["error"] for r in runs if r["error"]],
            "details": runs,
        })

    print(f"\ntranscript {chars} chars · num_ctx {args.num_ctx} · format {args.format} · think {args.think}\n")
    print("| model | runs | seconds (median) | valid JSON 1st try | entities | facts | episodes | procedures |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        line = (f"| {r['model']} | {r['runs']} | {r['seconds_median']} | {r['valid_json_first_try']} | "
                f"{r['entities']:g} | {r['facts']:g} | {r['episodes']:g} | {r['procedures']:g} |")
        if r["errors"]:
            line += " " + "; ".join(r["errors"])
        print(line)
    args.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"\nraw results: {args.out}")


if __name__ == "__main__":
    main()
