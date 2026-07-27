#!/usr/bin/env python3
"""Extraction quality evals — the boring moat.

Runs golden cases from extraction_cases.yaml through the real
ConversationExtractor (same construction path as cloud/api.py) and checks
expectations. Every real user complaint becomes a case; this suite must pass
before any change to extraction prompts ships.

Usage:
    OPENAI_API_KEY=... LLM_PROVIDER=openai LLM_MODEL=gpt-5.4-mini \
        python3 evals/run_extraction_evals.py [--case ID] [--verbose]

Exit code 0 = all pass.
"""
import argparse
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.extractor.llm_client import create_llm_client
from engine.extractor.conversation_extractor import ConversationExtractor


def build_extractor() -> ConversationExtractor:
    """Mirror cloud/api.py's construction exactly — evals must test prod's path."""
    llm_model = os.environ.get("LLM_MODEL", "")
    config = {
        "provider": os.environ.get("LLM_PROVIDER", "openai"),
        "anthropic": {"api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
                      **({"model": llm_model} if llm_model else {})},
        "openai": {"api_key": os.environ.get("OPENAI_API_KEY", ""),
                   **({"model": llm_model} if llm_model else {})},
    }
    return ConversationExtractor(create_llm_client(config))


def all_fact_text(result) -> str:
    parts = []
    for e in result.entities:
        for f in e.facts:
            parts.append(getattr(f, "content", str(f)))
    for k in result.knowledge:
        parts.append(getattr(k, "content", str(k)))
    return " \n ".join(parts).lower()


def everything_text(result) -> str:
    parts = [all_fact_text(result)]
    for ep in result.episodes:
        parts.append(f"{ep.summary} {ep.context} {ep.outcome}".lower())
    for p in result.procedures:
        parts.append(p.name.lower())
        for s in p.steps:
            if isinstance(s, dict):
                parts.append(f"{s.get('action', '')} {s.get('detail', '')}".lower())
    return " \n ".join(parts)


def run_case(extractor, case, verbose=False):
    failures = []
    result = extractor.extract(
        case["conversation"],
        existing_context=case.get("existing_context", ""),
        prompt_version="v2",
    )
    facts = all_fact_text(result)
    everything = everything_text(result)

    if verbose:
        print(f"    entities={[(e.name, len(e.facts)) for e in result.entities]}")
        print(f"    episodes={len(result.episodes)} procedures={len(result.procedures)}")

    if case.get("expect_no_output"):
        n = sum(len(e.facts) for e in result.entities) + len(result.knowledge) + len(result.procedures)
        if n > 0:
            failures.append(f"expected nothing, extracted {n} items: {facts[:150]}")

    for kw in case.get("must_extract", []):
        if kw.lower() not in everything:
            failures.append(f"missing required keyword: {kw!r}")

    for key in ("must_not_extract", "must_not_extract_for_primary", "must_not_extract_as_current"):
        for kw in case.get(key, []):
            if key == "must_not_extract" and kw.lower() in everything:
                failures.append(f"forbidden keyword present: {kw!r}")
            elif key != "must_not_extract" and kw.lower() in facts:
                # softer checks: forbidden only among FACTS (episodes may mention)
                failures.append(f"{key}: {kw!r} present in facts")

    if proc := case.get("expect_procedure"):
        matches = [p for p in result.procedures if proc["name_keyword"].lower() in p.name.lower()]
        if not matches:
            failures.append(f"no procedure matching {proc['name_keyword']!r} "
                            f"(got: {[p.name for p in result.procedures]})")
        else:
            p = matches[0]
            if len(p.steps) < proc.get("min_steps", 1):
                failures.append(f"procedure has {len(p.steps)} steps, expected >= {proc['min_steps']}")
            if p.steps and not isinstance(p.steps[0], dict):
                failures.append("REGRESSION: steps are not list[dict]")

    if kw := case.get("expect_episode_keyword"):
        if not any(kw.lower() in f"{ep.summary} {ep.context}".lower() for ep in result.episodes):
            failures.append(f"no episode mentioning {kw!r}")

    if tag := case.get("expect_category_tag"):
        # v0: category tagging surfaced via fact metadata; treated as advisory until
        # the extractor emits categories — report, don't fail.
        if tag not in everything:
            failures.append(f"ADVISORY: no {tag!r} category signal (capture-policy relies on it)")

    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="run a single case id")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cases = yaml.safe_load(open(Path(__file__).parent / "extraction_cases.yaml"))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"no case {args.case!r}"); sys.exit(2)

    extractor = build_extractor()
    passed = failed = advisory = 0
    for case in cases:
        print(f"  {case['id']} ...", flush=True)
        try:
            failures = run_case(extractor, case, verbose=args.verbose)
        except Exception as e:
            failures = [f"CRASH: {e}"]
        hard = [f for f in failures if not f.startswith("ADVISORY")]
        soft = [f for f in failures if f.startswith("ADVISORY")]
        advisory += len(soft)
        for f in soft:
            print(f"    ~ {f}")
        if hard:
            failed += 1
            for f in hard:
                print(f"    ✗ {f}")
        else:
            passed += 1
            print("    ✓ pass")

    print(f"\n{passed}/{passed + failed} passed" + (f" · {advisory} advisory" if advisory else ""))
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
