#!/usr/bin/env python3
"""Extraction quality evals — the boring moat.

Runs golden cases from extraction_cases.yaml through the real
ConversationExtractor and provider clients, with eval-specific model settings, and checks
expectations. Every real user complaint becomes a case; this suite must pass
before any change to extraction prompts ships.

Usage:
    OPENAI_API_KEY=... LLM_PROVIDER=openai LLM_MODEL=gpt-5.4-mini \
        python3 evals/run_extraction_evals.py [--case ID] [--verbose]

Exit code 0 = all pass.
"""
import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

import yaml
from jsonschema import ValidationError, validate

sys.path.insert(0, str(Path(__file__).parent.parent))

from engine.extractor.llm_client import create_llm_client
from engine.extractor.conversation_extractor import ConversationExtractor, EXTRACTION_SCHEMA


def build_extractor() -> ConversationExtractor:
    """Use the production factory; model/endpoint selection belongs to this harness."""
    llm_model = os.environ.get("LLM_MODEL", "")
    config = {
        "provider": os.environ.get("LLM_PROVIDER", "openai"),
        "anthropic": {"api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
                      **({"model": llm_model} if llm_model else {})},
        "openai": {"api_key": os.environ.get("OPENAI_API_KEY", ""),
                   **({"model": llm_model} if llm_model else {})},
        "ollama": {"base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
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


def run_case(extractor, case, verbose=False, prompt_version="v2"):
    failures = []
    result = extractor.extract(
        case["conversation"],
        existing_context=case.get("existing_context", ""),
        prompt_version=prompt_version,
    )
    facts = all_fact_text(result)
    everything = everything_text(result)
    extracted_text = json.dumps({k: v for k, v in asdict(result).items()
                                 if k != "raw_response"}, ensure_ascii=False).lower()
    if case.get("require_complete_json"):
        try:
            raw = json.loads(result.raw_response)
            validate(raw, EXTRACTION_SCHEMA["json_schema"]["schema"])
        except (ValueError, TypeError):
            failures.append("incomplete or invalid JSON response")
        except ValidationError as e:
            failures.append(f"JSON schema violation: {e.message}")
    for key in case.get("expect_nonempty_types", []):
        if not getattr(result, key):
            failures.append(f"missing output type: {key}")

    if verbose:
        print(f"    entities={[(e.name, len(e.facts)) for e in result.entities]}")
        print(f"    episodes={len(result.episodes)} procedures={len(result.procedures)}")
        print(f"    raw_response={result.raw_response!r}")

    if case.get("expect_no_output"):
        n = sum(len(getattr(result, key)) for key in
                ("entities", "relations", "knowledge", "episodes", "procedures"))
        if n > 0:
            failures.append(f"expected nothing, extracted {n} items: {facts[:150]}")

    for kw in case.get("must_extract", []):
        if kw.lower() not in everything:
            failures.append(f"missing required keyword: {kw!r}")

    for kw in case.get("must_not_extract", []):
        if kw.lower() in extracted_text:
            failures.append(f"forbidden keyword present: {kw!r}")

    for kw in case.get("must_not_extract_as_current", []):
        if kw.lower() in facts:
            failures.append(f"must_not_extract_as_current: {kw!r} present in facts")

    # Per-entity attribution (the #54 concern): a fact must land on the RIGHT
    # entity. {entity_keyword, must_have: [...], must_not_have: [...]}
    for att in case.get("expect_attribution", []):
        matches = [e for e in result.entities if att["entity_keyword"].lower() in e.name.lower()]
        if not matches:
            failures.append(f"attribution: no entity matching {att['entity_keyword']!r} "
                            f"(got {[e.name for e in result.entities]})")
            continue
        ent_facts = " ".join(getattr(f, "content", "") for e in matches for f in e.facts).lower()
        for kw in att.get("must_have", []):
            if kw.lower() not in ent_facts:
                failures.append(f"attribution: {att['entity_keyword']!r} missing {kw!r}")
        for kw in att.get("must_not_have", []):
            if kw.lower() in ent_facts:
                failures.append(f"attribution: {att['entity_keyword']!r} wrongly has {kw!r}")
        if one_of := att.get("must_have_one_of", []):
            if not any(kw.lower() in ent_facts for kw in one_of):
                failures.append(f"attribution: {att['entity_keyword']!r} missing all of {one_of} "
                                f"(facts: {ent_facts[:120]})")

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

    # Supersession: every fact-line mentioning the keyword must carry a past marker.
    if qp := case.get("qualified_past"):
        kw = qp["keyword"].lower()
        lines_with_kw = [ln for ln in everything.split(" \n ") if kw in ln]
        if not lines_with_kw:
            failures.append(f"qualified_past: {qp['keyword']!r} not mentioned at all")
        else:
            bad = [ln for ln in lines_with_kw if not any(m in ln for m in qp["markers"])]
            if bad:
                failures.append(f"qualified_past: {qp['keyword']!r} mentioned as current "
                                f"(no past marker): {bad[0][:100]!r}")

    # Capture-policy end-to-end: extracted facts are run through the REAL
    # deterministic deny-filter; sensitive facts must drop, work facts must stay.
    if cp := case.get("expect_capture_drop"):
        from cloud.store import CloudStore
        deny = CloudStore._compile_capture_policy({"deny_categories": cp["deny_categories"]})
        fact_list = [getattr(f, "content", "") for e in result.entities for f in e.facts]
        kept, dropped = CloudStore.apply_capture_policy_to_facts(fact_list, deny)
        dropped_text = " ".join(dropped).lower()
        kept_text = " ".join(kept).lower()
        if cp["dropped_keyword"].lower() not in dropped_text:
            failures.append(f"capture-policy: expected a fact with {cp['dropped_keyword']!r} to be "
                            f"DROPPED (dropped={dropped}, kept={kept})")
        if cp["kept_keyword"].lower() not in kept_text:
            failures.append(f"capture-policy: expected {cp['kept_keyword']!r} to SURVIVE "
                            f"(kept={kept})")

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
    ap.add_argument("--prompt-version", choices=("v1", "v2", "slim"), default="v2")
    args = ap.parse_args()

    cases = yaml.safe_load(open(Path(__file__).parent / "extraction_cases.yaml"))
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
        if not cases:
            print(f"no case {args.case!r}"); sys.exit(2)

    extractor = build_extractor()
    print(f"provider={os.environ.get('LLM_PROVIDER', 'openai')} "
          f"model={getattr(extractor.llm, 'model', 'unknown')} prompt_version={args.prompt_version}")
    passed = failed = advisory = known = 0
    for case in cases:
        print(f"  {case['id']} ...", flush=True)
        try:
            failures = run_case(extractor, case, verbose=args.verbose, prompt_version=args.prompt_version)
        except Exception as e:
            failures = [f"CRASH: {e}"]
        hard = [f for f in failures if not f.startswith("ADVISORY")]
        soft = [f for f in failures if f.startswith("ADVISORY")]
        advisory += len(soft)
        for f in soft:
            print(f"    ~ {f}")
        if case.get("known_gap"):
            # Reports failures but does not fail the suite — a tracked TODO, not a regression.
            known += 1
            for f in hard:
                print(f"    ⊘ known-gap: {f}")
            if not hard:
                print("    ✓ pass (known-gap resolved — consider removing the flag)")
        elif hard:
            failed += 1
            for f in hard:
                print(f"    ✗ {f}")
        else:
            passed += 1
            print("    ✓ pass")

    tail = f" · {advisory} advisory" if advisory else ""
    tail += f" · {known} known-gap" if known else ""
    print(f"\n{passed}/{passed + failed} enforced passed{tail}")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
