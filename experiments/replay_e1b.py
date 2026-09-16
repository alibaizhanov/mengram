"""E1b replay: chunk policies under the same budget, on memories E1 already built.
Usage: MENGRAM_API_KEY=... MENGRAM_BASE_URL=http://localhost:8420 OPENAI_API_KEY=... python3 replay_e1b.py"""
import json, os, sys, statistics
from pathlib import Path
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from cloud.client import CloudMemory
from cloud.budget import fit_search_all
from cloud.salience import content_words
from bench_memory import render_search_all, answer, score_answer, _matches, estimate_tokens

SUBS = ["bench-e1-on-1-companion-L", "bench-e1-on-2-companion-L", "bench-e1-on-4-companion-L"]
BUDGET = 600

def dedup(chunks, thr=0.6):
    kept = []
    for c in chunks:
        w = content_words(c.get("content") or c.get("text") or "")
        if any(len(w & content_words(k.get("content") or k.get("text") or "")) / max(1, len(w | content_words(k.get("content") or k.get("text") or ""))) > thr for k in kept):
            continue
        kept.append(c)
    return kept

POLICIES = {
    "P0_5chunks": lambda r: r,
    "P1_2chunks": lambda r: {**r, "chunks": (r.get("chunks") or [])[:2]},
    "P2_1chunk":  lambda r: {**r, "chunks": (r.get("chunks") or [])[:1]},
    "P3_nochunks": lambda r: {**r, "chunks": []},
    "P4_dedup2":  lambda r: {**r, "chunks": dedup(r.get("chunks") or [])[:2]},
}

def main():
    mem = CloudMemory(api_key=os.environ["MENGRAM_API_KEY"], base_url=os.environ.get("MENGRAM_BASE_URL"))
    truth = json.load(open(HERE / "corpus" / "companion_L.truth.json"))
    needed = [c["correct"].lower() for c in truth["cases"]]; dist = [c["distractor"].lower() for c in truth["cases"]]
    raw = {}  # (sub, qid) -> uncut result
    for sub in SUBS:
        for c in truth["cases"]:
            raw[(sub, c["id"])] = mem.search_all(c["question"], limit=5, user_id=sub)
    out = {}
    for pname, pol in POLICIES.items():
        rec, toks, fjunk, chunk_share = [], [], [], []
        for sub in SUBS:
            hits = 0
            for c in truth["cases"]:
                res, _ = fit_search_all(pol(json.loads(json.dumps(raw[(sub, c["id"])]))), BUDGET)
                ctx = render_search_all(res)
                ans = answer(c["question"], ctx)
                v = score_answer(ans, c["correct"], c["distractor"])
                hits += v == "correct"
                toks.append(estimate_tokens(ctx))
                facts = [l.strip()[2:] for l in ctx.splitlines() if l.startswith("  - ")]
                if facts:
                    fjunk.append(sum(1 for f in facts if not any(_matches(f, x) for x in needed + dist)) / len(facts))
                ch = "\n- ".join(ctx.split("\n- ")[1:])
                chunk_share.append(estimate_tokens(ch) / max(1, estimate_tokens(ctx)))
            rec.append(hits / len(truth["cases"]))
        out[pname] = {"recall_at_old_mean": round(statistics.mean(rec), 3), "runs": [round(r, 3) for r in rec],
                      "tokens_per_question": round(statistics.mean(toks)), "fact_junk_share": round(statistics.mean(fjunk), 2) if fjunk else None,
                      "chunk_token_share": round(statistics.mean(chunk_share), 2)}
        print(pname, json.dumps(out[pname]), flush=True)
    (HERE / "results" / "e1b-replay.json").write_text(json.dumps(out, indent=1))

if __name__ == "__main__":
    main()
