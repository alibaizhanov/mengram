#!/usr/bin/env python3
"""
ProcInterfere — the first benchmark for cross-procedure interference in agent memory.

Every 2025-26 procedural-memory system (MACLA arXiv:2512.18950, PRAXIS 2511.22074,
Memp 2508.06433, EvoSkill 2603.02766, AFTER 2606.23127) evaluates learned skills
in ISOLATION. None test what happens when revising procedure A silently breaks
procedure B that depended on it. This measures exactly that.

Metric: silent-regression rate = % of B-breaking revisions a system promotes
without flagging. Lower is better. A perfect system flags every breaking
revision and none of the safe ones.

Baselines:
  latest-wins   — the industry default: newest version always wins, no check.
  append-only   — keep all versions, still serve the newest; no interference check.
  mengram-gate  — Mengram's cross-procedure regression gate (cloud/regression_gate.py).

Run:  python run.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from cloud.regression_gate import find_regressions


def load_cases():
    path = Path(__file__).parent / "cases.jsonl"
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    # Assign distinct ids (real DB rows always have them; the gate treats two
    # id-less procedures as the same object).
    for c in cases:
        c["old"]["id"] = c["id"] + "-A-old"
        c["revision"]["id"] = c["id"] + "-A-new"
        c["dependent"]["id"] = c["id"] + "-B"
    return cases


# --- systems under test: does this system FLAG the revision as risky? ---------
def latest_wins(case) -> bool:
    return False  # promotes unconditionally, never flags

def append_only(case) -> bool:
    return False  # keeps history but still serves newest unchecked

def mengram_gate(case) -> bool:
    regs = find_regressions(case["old"], case["revision"], [case["dependent"]])
    return bool(regs)


SYSTEMS = {
    "latest-wins": latest_wins,
    "append-only": append_only,
    "mengram-gate": mengram_gate,
}


def main():
    cases = load_cases()
    breaking = [c for c in cases if c["should_flag"]]
    safe = [c for c in cases if not c["should_flag"]]

    print(f"ProcInterfere — {len(cases)} cases ({len(breaking)} breaking, {len(safe)} safe)\n")
    print(f"{'system':<14} {'silent-regression':>18} {'false-quarantine':>18}")
    print("-" * 52)
    rows = []
    for name, fn in SYSTEMS.items():
        # silent regression: a breaking revision the system did NOT flag
        missed = sum(1 for c in breaking if not fn(c))
        # false quarantine: a safe revision the system wrongly flagged
        false_pos = sum(1 for c in safe if fn(c))
        sr = missed / len(breaking) if breaking else 0
        fq = false_pos / len(safe) if safe else 0
        rows.append((name, sr, fq))
        print(f"{name:<14} {sr:>17.0%} {fq:>17.0%}")

    print("\nSilent-regression rate = breaking revisions promoted without a flag (lower better).")
    best = min(rows, key=lambda r: (r[1], r[2]))
    print(f"Best: {best[0]} — {best[1]:.0%} silent regressions, {best[2]:.0%} false quarantines.")

    # exit non-zero if mengram-gate isn't clearly best (guards against regressions in the gate)
    mg = next(r for r in rows if r[0] == "mengram-gate")
    baseline_sr = min(r[1] for r in rows if r[0] != "mengram-gate")
    ok = mg[1] < baseline_sr
    print("\nPASS" if ok else "\nFAIL: gate no better than baselines")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
