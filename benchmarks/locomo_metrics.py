"""
LoCoMo Benchmark Scoring — exact implementation of the LoCoMo paper methodology.

Metrics:
  - Token-level F1 with Porter stemming (primary, from LoCoMo ACL 2024)
  - Multi-hop F1: split by comma, partial F1 per sub-answer, mean
  - Adversarial: binary match on "not mentioned" / "no information available"
  - LLM-as-judge: Claude binary CORRECT/WRONG (Backboard-style)

Reference: https://github.com/snap-research/locomo/blob/main/task_eval/evaluation.py
"""

import re
import string
from collections import Counter

import numpy as np

try:
    import Stemmer  # PyStemmer — lightweight, C-based
    _stemmer = Stemmer.Stemmer("english")
    def _stem(word: str) -> str:
        return _stemmer.stemWord(word)
except ImportError:
    try:
        from nltk.stem import PorterStemmer
        _ps = PorterStemmer()
        def _stem(word: str) -> str:
            return _ps.stem(word)
    except ImportError:
        def _stem(word: str) -> str:
            return word  # no stemming fallback

CATEGORY_NAMES = {
    1: "multi-hop",
    2: "temporal",
    3: "open-domain",
    4: "single-hop",
    5: "adversarial",
}


# ── Normalization (matches LoCoMo paper exactly) ──────────────────────

def normalize_answer(s: str) -> str:
    """Lowercase, remove commas/punctuation/articles, fix whitespace."""
    s = s.replace(",", "")
    s = s.lower()
    s = "".join(ch for ch in s if ch not in string.punctuation)
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    s = " ".join(s.split())
    return s


# ── Token-level F1 ───────────────────────────────────────────────────

def f1_score_single(prediction: str, ground_truth: str) -> float:
    """Token-level F1 with Porter stemming."""
    pred_tokens = [_stem(w) for w in normalize_answer(prediction).split()]
    gt_tokens = [_stem(w) for w in normalize_answer(ground_truth).split()]
    if not pred_tokens or not gt_tokens:
        return 0.0
    common = Counter(pred_tokens) & Counter(gt_tokens)
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gt_tokens)
    return (2 * precision * recall) / (precision + recall)


def f1_multi_hop(prediction: str, ground_truth: str) -> float:
    """Multi-hop: split by comma, partial F1 per sub-answer, mean of best matches."""
    predictions = [p.strip() for p in prediction.split(",") if p.strip()]
    ground_truths = [g.strip() for g in ground_truth.split(",") if g.strip()]
    if not predictions or not ground_truths:
        return 0.0
    scores = []
    for gt in ground_truths:
        best = max(f1_score_single(p, gt) for p in predictions)
        scores.append(best)
    return float(np.mean(scores))


def score_adversarial(prediction: str, ground_truth: str = "not mentioned") -> float:
    """Adversarial: 1 if model correctly identifies info as unavailable,
    or correctly answers when GT is a definite answer (e.g. 'No')."""
    gt_lower = ground_truth.lower().strip()
    # If GT is a definite answer (not "not mentioned"), use F1 scoring
    if gt_lower not in ("not mentioned", "no information available"):
        return f1_score_single(prediction, ground_truth)
    # Standard adversarial: check if model says "not mentioned"
    lower = prediction.lower()
    if "no information available" in lower or "not mentioned" in lower:
        return 1.0
    return 0.0


# ── Main scorer dispatcher ───────────────────────────────────────────

def score_qa(prediction: str, ground_truth: str, category: int) -> float:
    """Score a single QA pair using the appropriate metric for its category.

    Categories: 1=multi-hop, 2=temporal, 3=open-domain, 4=single-hop, 5=adversarial
    """
    if category == 5:
        return score_adversarial(prediction, ground_truth)

    # Open-domain: use only first sub-answer before ";"
    if category == 3:
        ground_truth = ground_truth.split(";")[0].strip()

    # Multi-hop: comma-separated sub-answers
    if category == 1:
        return f1_multi_hop(prediction, ground_truth)

    # Categories 2, 3, 4: standard single F1
    return f1_score_single(prediction, ground_truth)


# ── LLM-as-Judge ─────────────────────────────────────────────────────

LLM_JUDGE_PROMPT = """You are evaluating a question-answering system.
Given a question, the ground-truth answer, and the system's predicted answer,
determine if the prediction is CORRECT or WRONG.

Question: {question}
Ground truth: {ground_truth}
Prediction: {prediction}

The prediction is CORRECT if it conveys the same factual information as the ground truth,
even if worded differently. Minor differences in phrasing, dates format, or name order
are acceptable. For time-related questions, be generous ("May 7th" vs "7 May" is CORRECT).

Respond with exactly one word: CORRECT or WRONG"""


def llm_judge_score(
    anthropic_client,
    question: str,
    ground_truth: str,
    prediction: str,
    model: str = "claude-sonnet-4-20250514",
) -> float:
    """LLM-as-judge: binary CORRECT/WRONG."""
    prompt = LLM_JUDGE_PROMPT.format(
        question=question,
        ground_truth=ground_truth,
        prediction=prediction,
    )
    response = anthropic_client.messages.create(
        model=model,
        max_tokens=10,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    verdict = response.content[0].text.strip().upper()
    return 1.0 if "CORRECT" in verdict else 0.0


# ── Aggregation ──────────────────────────────────────────────────────

def compute_aggregate_scores(results: list[dict]) -> dict:
    """Compute per-category and overall scores."""
    by_category: dict[int, list[dict]] = {c: [] for c in range(1, 6)}
    all_f1 = []
    all_judge = []

    for r in results:
        cat = r["category"]
        f1 = r["f1_score"]
        by_category[cat].append(r)
        all_f1.append(f1)
        if "llm_judge_score" in r:
            all_judge.append(r["llm_judge_score"])

    summary = {
        "overall_f1": float(np.mean(all_f1)) if all_f1 else 0.0,
        "overall_llm_judge": float(np.mean(all_judge)) if all_judge else None,
        "total_questions": len(results),
        "per_category": {},
    }

    for cat in range(1, 6):
        cat_results = by_category[cat]
        if cat_results:
            cat_f1 = [r["f1_score"] for r in cat_results]
            cat_judge = [r["llm_judge_score"] for r in cat_results
                         if "llm_judge_score" in r]
            summary["per_category"][CATEGORY_NAMES[cat]] = {
                "count": len(cat_results),
                "f1_mean": round(float(np.mean(cat_f1)), 4),
                "f1_std": round(float(np.std(cat_f1)), 4),
                "llm_judge_mean": round(float(np.mean(cat_judge)), 4)
                if cat_judge else None,
            }

    return summary


# ── Pretty-print ─────────────────────────────────────────────────────

def print_results_table(summary: dict) -> None:
    """Print formatted results table to terminal."""
    header = f"{'Category':<15} {'Count':>5} {'F1':>8} {'Std':>8} {'Judge':>8}"
    sep = "-" * len(header)
    print(header)
    print(sep)

    display_order = ["single-hop", "multi-hop", "temporal", "open-domain", "adversarial"]
    for cat_name in display_order:
        data = summary["per_category"].get(cat_name, {})
        count = data.get("count", 0)
        if count == 0:
            continue
        f1 = data.get("f1_mean", 0)
        std = data.get("f1_std", 0)
        judge = data.get("llm_judge_mean")
        judge_str = f"{judge:.3f}" if judge is not None else "N/A"
        print(f"{cat_name:<15} {count:>5} {f1:>8.3f} {std:>8.3f} {judge_str:>8}")

    print(sep)
    overall_f1 = summary.get("overall_f1", 0)
    overall_judge = summary.get("overall_llm_judge")
    judge_str = f"{overall_judge:.3f}" if overall_judge is not None else "N/A"
    total = summary.get("total_questions", 0)
    print(f"{'OVERALL':<15} {total:>5} {overall_f1:>8.3f} {'':>8} {judge_str:>8}")
    print()
    print("Competitors:  Mem0 ~68%  |  Zep ~75%  |  Backboard ~90%")
