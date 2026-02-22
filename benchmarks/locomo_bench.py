#!/usr/bin/env python3
"""
LoCoMo Benchmark for Mengram Memory System.

Evaluates Mengram against the LoCoMo (Long Conversation Memory) benchmark —
the industry standard for AI agent memory systems.

Pipeline:
    1. Ingest all 10 LoCoMo conversations into Mengram (async, ~30-60 min)
    2. For each QA question: retrieve context → generate answer via LLM → score
    3. Output per-category and overall F1 scores + optional LLM-as-judge

Usage:
    python benchmarks/locomo_bench.py --api-key om-...
    python benchmarks/locomo_bench.py --skip-ingest --skip-judge  # fast re-eval
    python benchmarks/locomo_bench.py --max-conversations 1       # smoke test
    python benchmarks/locomo_bench.py --base-url http://localhost:8420  # local

Reference: https://github.com/snap-research/locomo
Competitors: Mem0 ~68% | Zep ~75% | Backboard ~90%
"""

import os
import sys
import json
import time
import argparse

# Ensure project root is on path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from cloud.client import CloudMemory
from importer import RateLimiter
from benchmarks.locomo_metrics import (
    score_qa, llm_judge_score, compute_aggregate_scores,
    print_results_table, CATEGORY_NAMES,
)

# ── Constants ────────────────────────────────────────────────────────

STATE_DIR = os.path.join(_ROOT, "benchmarks", "results")
STATE_FILE = os.path.join(STATE_DIR, "locomo_state.json")
RESULTS_FILE = os.path.join(STATE_DIR, "locomo_results.json")

MAX_CONTEXT_CHARS = 12000  # ~3000 tokens
CHUNK_SIZE = 20  # messages per /v1/add call
MAX_RETRIES = 3  # retry 502/503 errors
RETRY_BACKOFF_S = 10  # seconds between retries

ANSWER_PROMPT = """You are answering questions about conversations between two people.
Use ONLY the retrieved context below to answer.

Retrieved context:
{context}

Question: {question}

Rules:
- Answer in 1-20 words. Be direct and concise.
- ONLY answer based on what is EXPLICITLY stated in the context about the SPECIFIC person or thing asked about.
- If the question asks about Person X, but the context only mentions Person Y doing that thing, answer "not mentioned" — do NOT substitute one person for another.
- If the question asks about something not covered in the context, answer "not mentioned".
- For date/time questions, look for dates in [brackets] like [8 May, 2023] in conversation excerpts, or date references in facts.
- If the question asks for multiple items, list them separated by commas.
- Do not explain reasoning. Just give the answer.

Answer:"""


# ── State Management ─────────────────────────────────────────────────

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            state = json.load(f)
        # Convert list back to set for fast lookup
        state["completed_qa_keys"] = set(state.get("completed_qa_keys", []))
        return state
    return {"ingested": {}, "qa_results": [], "completed_qa_keys": set()}


def save_state(state: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    s = state.copy()
    s["completed_qa_keys"] = list(s.get("completed_qa_keys", set()))
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)


# ── Dataset Loading ──────────────────────────────────────────────────

def load_locomo(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def extract_sessions(conversation: dict) -> list[tuple[int, str, list[dict]]]:
    """Extract ordered (session_num, datetime_str, turns) from conversation."""
    sessions = []
    i = 1
    while f"session_{i}" in conversation:
        ts = conversation.get(f"session_{i}_date_time", f"Session {i}")
        turns = conversation[f"session_{i}"]
        sessions.append((i, ts, turns))
        i += 1
    return sessions


# ── Ingestion ────────────────────────────────────────────────────────

def format_session_messages(
    session_num: int,
    timestamp: str,
    turns: list[dict],
    speaker_a: str,
    speaker_b: str,
) -> list[dict]:
    """Convert LoCoMo session turns into Mengram-compatible messages."""
    messages = []

    # Temporal context marker
    messages.append({
        "role": "user",
        "content": f"[Conversation session {session_num} — {timestamp}]"
    })
    messages.append({
        "role": "assistant",
        "content": f"Continuing conversation on {timestamp}."
    })

    for i, turn in enumerate(turns):
        speaker = turn["speaker"]
        text = turn["text"]

        # Include image context if present
        if turn.get("blip_caption"):
            text += f" [Shared an image: {turn['blip_caption']}]"

        # Both speakers as "user" with assistant acks for API turn structure
        # This ensures extraction prompt processes ALL speakers equally
        content = f"[{timestamp}] {speaker}: {text}"
        messages.append({"role": "user", "content": content})
        messages.append({"role": "assistant", "content": "noted"})

    return messages


def ingest_conversation(
    client: CloudMemory,
    sample_idx: int,
    sample: dict,
    state: dict,
    rate_limiter: RateLimiter,
) -> None:
    """Ingest one LoCoMo conversation into Mengram."""
    user_id = f"locomo_{sample_idx}"
    key = str(sample_idx)

    if state["ingested"].get(key):
        print(f"  [conv {sample_idx}] Already ingested, skipping.")
        return

    conversation = sample["conversation"]
    speaker_a = conversation["speaker_a"]
    speaker_b = conversation["speaker_b"]
    sessions = extract_sessions(conversation)

    total_chunks = 0
    for session_num, timestamp, turns in sessions:
        messages = format_session_messages(
            session_num, timestamp, turns, speaker_a, speaker_b
        )

        # Chunk into batches
        for chunk_start in range(0, len(messages), CHUNK_SIZE):
            chunk = messages[chunk_start:chunk_start + CHUNK_SIZE]

            # Retry with backoff for any transient error (DNS, 502, timeout, etc)
            for attempt in range(MAX_RETRIES):
                rate_limiter.wait_if_needed()
                try:
                    result = client.add(chunk, user_id=user_id)
                    job_id = result.get("job_id")
                    if job_id:
                        client.wait_for_job(job_id, max_wait=180)
                    total_chunks += 1
                    break  # success
                except Exception as e:
                    wait = RETRY_BACKOFF_S * (attempt + 1)
                    if attempt < MAX_RETRIES - 1:
                        print(f"    RETRY {attempt + 1}/{MAX_RETRIES} "
                              f"(wait {wait}s): {e}")
                        time.sleep(wait)
                    else:
                        print(f"    FAILED after {MAX_RETRIES} attempts: {e}")

        print(f"    Session {session_num}/{len(sessions)} done "
              f"({len(turns)} turns)")

    state["ingested"][key] = True
    save_state(state)
    print(f"  [conv {sample_idx}] Ingested {total_chunks} chunks "
          f"from {len(sessions)} sessions.")


# ── Retrieval ────────────────────────────────────────────────────────

def retrieve_context(
    client: CloudMemory,
    question: str,
    category: int,
    user_id: str,
    rate_limiter: RateLimiter,
) -> str:
    """Retrieve relevant context from Mengram for a question."""
    context_parts = []

    # Primary: unified search across all memory types — with retry
    limit = 20 if category in (1, 2) else 15
    results = {}
    for attempt in range(MAX_RETRIES):
        rate_limiter.wait_if_needed()
        try:
            results = client.search_all(question, limit=limit, user_id=user_id)
            break
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                time.sleep(5 * (attempt + 1))
            else:
                print(f"    search_all failed: {e}")

    # Format semantic results — include ALL facts, not just first 5
    for entity in results.get("semantic", []):
        name = entity.get("entity", entity.get("name", ""))
        etype = entity.get("type", "")
        facts = entity.get("facts", [])
        knowledge = entity.get("knowledge", [])
        relations = entity.get("relations", [])
        parts = []
        if facts:
            parts.append("; ".join(str(f) for f in facts[:10]))
        for k in knowledge[:3]:
            if isinstance(k, dict):
                parts.append(f"{k.get('title', '')}: {k.get('content', '')}")
        for r in relations[:3]:
            if isinstance(r, dict):
                target = r.get("target", "")
                rtype = r.get("type", "")
                if target and rtype:
                    parts.append(f"{rtype} → {target}")
        if parts:
            prefix = f"{name} ({etype})" if etype else name
            context_parts.append(f"{prefix}: {' | '.join(parts)}")

    # Format episodic results
    for ep in results.get("episodic", []):
        summary = ep.get("summary", "")
        context_str = ep.get("context", "")
        outcome = ep.get("outcome", "")
        ts = ep.get("created_at", "")
        parts = [summary]
        if context_str:
            parts.append(f"Context: {context_str}")
        if outcome:
            parts.append(f"Outcome: {outcome}")
        ts_prefix = f"[{ts}] " if ts else ""
        context_parts.append(f"{ts_prefix}Episode: {' '.join(parts)}")

    # Format procedural results
    for proc in results.get("procedural", []):
        name = proc.get("name", "")
        steps = proc.get("steps", [])
        if steps:
            step_texts = []
            for s in steps[:5]:
                if isinstance(s, dict):
                    step_texts.append(s.get("action", str(s)))
                else:
                    step_texts.append(str(s))
            context_parts.append(f"Procedure '{name}': {' → '.join(step_texts)}")

    # Raw conversation chunks (fallback for extraction misses)
    for chunk in results.get("chunks", []):
        content = chunk.get("content", "")
        if content:
            context_parts.append(f"[Conversation excerpt] {content[:800]}")

    # Temporal augmentation for category 2 (temporal reasoning)
    if category == 2:
        rate_limiter.wait_if_needed()
        try:
            timeline = client.timeline(user_id=user_id, limit=20)
            for item in timeline:
                fact = item.get("fact", item.get("content", ""))
                ts = item.get("event_date", item.get("timestamp",
                     item.get("created_at", "")))
                entity = item.get("entity", "")
                if fact:
                    context_parts.append(f"[Timeline {ts}] {entity}: {fact}")
        except Exception:
            pass

        rate_limiter.wait_if_needed()
        try:
            episodes = client.episodes(query=question, limit=5, user_id=user_id)
            for ep in episodes:
                summary = ep.get("summary", "")
                ts = ep.get("happened_at", ep.get("created_at", ""))
                if summary:
                    context_parts.append(f"[Episode {ts}] {summary}")
        except Exception:
            pass

    # Cap total context length
    combined = "\n".join(context_parts)
    if len(combined) > MAX_CONTEXT_CHARS:
        combined = combined[:MAX_CONTEXT_CHARS] + "\n[...truncated]"

    return combined


# ── Answer Generation ────────────────────────────────────────────────

def generate_answer(
    anthropic_client,
    question: str,
    context: str,
    model: str = "claude-sonnet-4-20250514",
) -> str:
    """Generate a short answer using Claude from retrieved context."""
    if not context.strip():
        return "not mentioned"

    prompt = ANSWER_PROMPT.format(context=context, question=question)

    response = anthropic_client.messages.create(
        model=model,
        max_tokens=100,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )

    return response.content[0].text.strip()


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="LoCoMo Benchmark for Mengram Memory System"
    )
    parser.add_argument("--api-key",
                        default=os.environ.get("MENGRAM_API_KEY"),
                        help="Mengram API key (or set MENGRAM_API_KEY env var)")
    parser.add_argument("--base-url",
                        default=os.environ.get("MENGRAM_BASE_URL", "https://mengram.io"),
                        help="Mengram API base URL (default: https://mengram.io)")
    parser.add_argument("--data",
                        default=os.path.join(_ROOT, "benchmarks", "data", "locomo10.json"),
                        help="Path to locomo10.json dataset")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Skip ingestion phase (data already loaded)")
    parser.add_argument("--skip-judge", action="store_true",
                        help="Skip LLM-as-judge scoring (faster)")
    parser.add_argument("--answer-model",
                        default="claude-sonnet-4-20250514",
                        help="Model for answer generation")
    parser.add_argument("--max-conversations", type=int, default=10,
                        help="Limit number of conversations to process")
    parser.add_argument("--anthropic-key",
                        default=os.environ.get("ANTHROPIC_API_KEY"),
                        help="Anthropic API key for answer generation (or set ANTHROPIC_API_KEY)")
    parser.add_argument("--reset", action="store_true",
                        help="Clear state and start fresh")
    args = parser.parse_args()

    if not args.api_key:
        print("ERROR: --api-key required (or set MENGRAM_API_KEY env var)")
        sys.exit(1)

    if not args.anthropic_key:
        print("ERROR: --anthropic-key required (or set ANTHROPIC_API_KEY env var)")
        print("This is needed for LLM answer generation from retrieved context.")
        sys.exit(1)

    if not os.path.exists(args.data):
        print(f"ERROR: Dataset not found at {args.data}")
        print("Download it with:")
        print("  mkdir -p benchmarks/data")
        print("  wget -O benchmarks/data/locomo10.json \\")
        print("    https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json")
        sys.exit(1)

    # Initialize
    client = CloudMemory(api_key=args.api_key, base_url=args.base_url)
    rate_limiter = RateLimiter(max_per_minute=100)

    import anthropic
    llm_client = anthropic.Anthropic(api_key=args.anthropic_key)

    # Load data
    print(f"Loading dataset from {args.data}...")
    samples = load_locomo(args.data)[:args.max_conversations]
    print(f"Loaded {len(samples)} conversations.\n")

    # State
    if args.reset and os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    state = load_state()

    # ── Phase 1: Ingestion ──

    if not args.skip_ingest:
        print("=" * 50)
        print("Phase 1: Ingesting conversations into Mengram")
        print("=" * 50)
        t0 = time.time()
        for idx, sample in enumerate(samples):
            conv = sample["conversation"]
            print(f"\n  Conversation {idx}: "
                  f"{conv['speaker_a']} & {conv['speaker_b']}")
            ingest_conversation(client, idx, sample, state, rate_limiter)
        elapsed = time.time() - t0
        print(f"\nIngestion complete in {elapsed:.0f}s.\n")
    else:
        print("Skipping ingestion (--skip-ingest).\n")

    # ── Phase 2: QA Evaluation ──

    print("=" * 50)
    print("Phase 2: QA Evaluation")
    print("=" * 50)

    completed_keys = state["completed_qa_keys"]
    total_qa = sum(len(s["qa"]) for s in samples)
    done_count = len(completed_keys)
    print(f"Total QA pairs: {total_qa}, already done: {done_count}\n")

    t0 = time.time()
    for sample_idx, sample in enumerate(samples):
        user_id = f"locomo_{sample_idx}"
        qa_list = sample["qa"]

        for qa_idx, qa in enumerate(qa_list):
            key = f"{sample_idx}_{qa_idx}"
            if key in completed_keys:
                continue

            question = qa["question"]
            category = qa["category"]
            # Adversarial questions (cat 5) have no "answer" key — correct answer is "not mentioned"
            ground_truth = str(qa["answer"]) if "answer" in qa else "not mentioned"
            cat_name = CATEGORY_NAMES.get(category, "?")

            # Retrieve context
            context = retrieve_context(
                client, question, category, user_id, rate_limiter
            )

            # Generate answer
            try:
                prediction = generate_answer(
                    llm_client, question, context, model=args.answer_model
                )
            except Exception as e:
                print(f"  ERROR generating answer: {e}")
                prediction = "not mentioned"

            # Score (token F1)
            f1 = score_qa(prediction, ground_truth, category)

            result = {
                "key": key,
                "sample_idx": sample_idx,
                "qa_idx": qa_idx,
                "category": category,
                "question": question,
                "ground_truth": ground_truth,
                "prediction": prediction,
                "context_length": len(context),
                "f1_score": f1,
            }

            # LLM-as-judge (optional, skip for adversarial)
            if not args.skip_judge and category != 5:
                try:
                    judge = llm_judge_score(
                        llm_client, question, ground_truth, prediction,
                        model=args.answer_model,
                    )
                    result["llm_judge_score"] = judge
                except Exception as e:
                    print(f"  WARNING: LLM judge failed: {e}")

            state["qa_results"].append(result)
            completed_keys.add(key)
            save_state(state)

            done_count += 1
            judge_str = ""
            if "llm_judge_score" in result:
                judge_str = f" judge={'CORRECT' if result['llm_judge_score'] > 0.5 else 'WRONG'}"
            print(f"  [{done_count}/{total_qa}] conv={sample_idx} "
                  f"cat={cat_name:<12} f1={f1:.3f}{judge_str} "
                  f"| {prediction[:50]}")

    elapsed = time.time() - t0
    print(f"\nQA evaluation complete in {elapsed:.0f}s.\n")

    # ── Phase 3: Results ──

    print("=" * 50)
    print("Phase 3: Results")
    print("=" * 50)
    print()

    summary = compute_aggregate_scores(state["qa_results"])
    print_results_table(summary)

    # Save results
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nResults saved to {RESULTS_FILE}")


if __name__ == "__main__":
    main()
