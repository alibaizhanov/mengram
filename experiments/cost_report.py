"""E2 cost report: context tokens per request, memory vs full history, from the
e2 runs. Prices are inputs, stated in the output, never hidden in a claim.
Usage: python3 cost_report.py [--price-per-m 0.15] [--requests-per-user-day 20] [--users 1000]"""
import argparse, glob, json, statistics
from pathlib import Path
HERE = Path(__file__).parent

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--price-per-m", type=float, default=0.15, help="USD per 1M input tokens (default gpt-4o-mini)")
    ap.add_argument("--requests-per-user-day", type=int, default=20)
    ap.add_argument("--users", type=int, default=1000)
    a = ap.parse_args()
    rows = {}
    # full-history numbers from e2; memory numbers from the latest e2* run
    # (e2b = after the gate fix E2 found), so the report reflects what ships.
    for p in sorted(glob.glob(str(HERE / "results" / "e2*" / "result.json"))):
        s = json.load(open(p))["summary"]; rows[(s["system"], s["type"], s["scale"])] = s
    print(f"Context tokens per request (exact, o200k_base), full history vs Mengram (gate on, 1 chunk, 600-token budget)")
    print(f"{'type':10}{'sc':3}{'days':>5}{'full':>8}{'mem':>7}{'ratio':>7}{'recall full':>12}{'recall mem':>11}")
    ratios = {}
    for t in ("companion", "support", "coding"):
        for sc in "SML":
            f = rows.get(("full", t, sc)); m = rows.get(("mengram", t, sc))
            if not f or not m:
                continue
            r = f["tokens_exact_per_question"] / max(1, m["tokens_exact_per_question"]); ratios.setdefault(sc, []).append(r)
            print(f"{t:10}{sc:3}{m['days']:>5}{f['tokens_exact_per_question']:>8}{m['tokens_exact_per_question']:>7}{r:>7.1f}{f['recall_at_old']:>12.2f}{m['recall_at_old']:>11.2f}")
    print()
    for sc in "SML":
        if sc in ratios:
            print(f"scale {sc}: ratio min {min(ratios[sc]):.1f}x  mean {statistics.mean(ratios[sc]):.1f}x")
    # money, stated assumptions
    req = a.requests_per_user_day * 30 * a.users
    print(f"\nAt ${a.price_per_m}/M input tokens, {a.users} users x {a.requests_per_user_day} requests/day x 30 days = {req:,} requests/month:")
    for sc in "SML":
        fs = [rows[k]["tokens_exact_per_question"] for k in rows if k[0] == "full" and k[2] == sc]
        ms = [rows[k]["tokens_exact_per_question"] for k in rows if k[0] == "mengram" and k[2] == sc]
        if fs and ms:
            cf = statistics.mean(fs) * req / 1e6 * a.price_per_m; cm = statistics.mean(ms) * req / 1e6 * a.price_per_m
            print(f"  {sc}: full history ${cf:,.0f}/month  vs  memory ${cm:,.0f}/month  (context input only; extraction and search costs not included)")

if __name__ == "__main__":
    main()
