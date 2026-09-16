#!/usr/bin/env python3
"""E0 — does the answer survive? A memory benchmark with a ruler anyone can re-run.

Design carried over from densely/bench_recall.py: the true fact is planted early
in a long dialogue, a plausible wrong value is planted late (a recency trap), and
the question cannot be answered from anything but memory. Three numbers per
system and scale, nothing else:

    recall@old   fraction of planted facts the system answered correctly
    junk_rate    fraction of what it STORED that no question ever needs
    tokens       estimated context tokens handed to the answer model, per question

Systems under test (all answered by the same model at temperature 0):
    full       the whole dialogue replayed — upper bound on recall and on tokens
    sandbox    a reproduction of the OpenAI Agents SDK sandbox memory (see
               growth/openai-agents-harness-2026-09-16.md): per-rollout
               summary + raw memory with a no-op gate, MEMORY.md rewritten by a
               second pass, memory_summary.md injected (2,500-token cap, Codex),
               keyword grep on demand (<= 6 steps), newest-kept consolidation
    mengram    the cloud API with a throwaway sub-user per run, search/all with
               a max_tokens budget

Corpus is synthetic with a fixed seed, so it is public and regenerable; the
ground truth sits next to it. Scales: S = 7 simulated days, M = 30, L = 90.

Usage:
    python3 bench_memory.py generate --out corpus
    OPENAI_API_KEY=... python3 bench_memory.py run --corpus corpus --system full --type support --scale S --run r1
    python3 bench_memory.py report --dir results
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cloud.budget import estimate_tokens  # noqa: E402
from cloud.client import _SSL_CTX  # noqa: E402  (certifi CAs: macOS python ships without a usable bundle)

SCALES = {"S": 7, "M": 30, "L": 90}
TYPES = ("companion", "support", "coding")
CASES_PER_SCALE = {"S": 6, "M": 12, "L": 20}
TURNS_PER_DAY = (6, 10)
ANSWER_MODEL = os.environ.get("BENCH_MODEL", "gpt-4o-mini")
OPENAI_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


# ---------------------------------------------------------------------------
# Corpus: a fixed cast, facts planted on known days, distractors planted later
# ---------------------------------------------------------------------------

# Each slot: question, pool of true values, pool of distractor values, how the
# user states the truth, how the distractor enters later (someone else's, or
# considered and rejected — the recency trap).
SLOTS = {
    "companion": [
        ("What is the name of my dog?", ["Biscuit", "Nala", "Pixel", "Mochi"], ["Rex", "Luna", "Toby"],
         "By the way, my dog is called {v}.", "My neighbour's dog {d} keeps barking at night."),
        ("Which city does my sister live in?", ["Lisbon", "Tallinn", "Porto", "Bergen"], ["Madrid", "Oslo", "Riga"],
         "My sister moved to {v} last spring.", "A friend from {d} visited me this weekend."),
        ("What am I allergic to?", ["peanuts", "shellfish", "kiwi", "penicillin"], ["gluten", "lactose", "soy"],
         "Reminder for you: I'm allergic to {v}, so never suggest it.", "My colleague is allergic to {d}, so we skipped that restaurant."),
        ("How do I take my coffee?", ["flat white", "long black", "cortado", "oat latte"], ["cappuccino", "espresso", "mocha"],
         "I always order a {v}.", "I tried a {d} today for a change, didn't like it."),
        ("What is my running goal this year?", ["a half marathon", "a 10k under 50 minutes", "300 km a month"], ["a full marathon", "a 5k"],
         "This year my goal is {v}.", "My brother is training for {d}."),
        ("What instrument do I play?", ["cello", "ukulele", "clarinet", "bass"], ["piano", "guitar", "violin"],
         "I've played the {v} since school.", "My daughter started {d} lessons."),
        ("Which month is my birthday?", ["March", "August", "November", "May"], ["June", "January", "October"],
         "My birthday is in {v}, if you ever need it.", "My mother's birthday is in {d}."),
        ("What is my job title?", ["data engineer", "product designer", "nurse", "architect"], ["teacher", "lawyer", "chef"],
         "Work-wise, I'm a {v}.", "My partner is a {d}, we compare notes."),
    ],
    "support": [
        ("Which transmission does the customer prefer?", ["automatic"], ["manual"],
         "I always rent an automatic, never manual.", "My brother took a {d} last time and hated it."),
        ("Where does the customer prefer to pick up the car?", ["the airport desk", "the central station office"], ["the downtown branch", "the harbour office"],
         "Please book pickup at {v} as usual.", "I considered {d} once but it's too far."),
        ("What is the customer's loyalty number?", ["LR-48213", "LR-90377", "LR-11560"], ["LR-48231", "LR-90733"],
         "My loyalty number is {v}.", "My wife's loyalty number is {d}, separate account."),
        ("Which insurance option does the customer take?", ["full coverage", "basic with excess"], ["no insurance", "third-party only"],
         "Put me down for {v} every time.", "A colleague went with {d} and regretted it."),
        ("Does the customer need a child seat?", ["yes, one child seat"], ["no child seat"],
         "We always need one child seat, please.", "For the business trip in spring {d} will be needed, that's my colleague's booking."),
        ("Which card does the customer pay with?", ["the Visa ending 4471", "the Amex ending 2210"], ["the Mastercard ending 9083"],
         "Charge {v}, same as always.", "I used {d} for a hotel, not for the car."),
        ("What is the customer's preferred car class?", ["compact", "estate", "SUV"], ["convertible", "van"],
         "A {v} is what I usually take.", "My neighbour rents a {d} every summer."),
        ("What does the customer want on return?", ["a full tank refill service", "an e-receipt to work email"], ["a paper receipt", "cash refund"],
         "On return I want {v}.", "My assistant asked about {d} for someone else."),
    ],
    "coding": [
        ("Which port does our production database use?", ["5432", "6543"], ["5433", "5439"],
         "For the record: production Postgres is on port {v}.", "The staging replica sits on port {d}, don't mix them up."),
        ("Where do we deploy the API?", ["Railway", "Fly.io", "Render"], ["Heroku", "Vercel"],
         "We deploy the API to {v}, main auto-deploys.", "The marketing site lives on {d}, different pipeline."),
        ("Which cache did we choose?", ["Redis", "Memcached"], ["Memcached", "Redis"],
         "Decision: we go with {v} for the cache.", "We evaluated {d} and rejected it because of the eviction model."),
        ("What is the command to run the test suite?", ["make test", "pytest -q tests/", "npm run test:ci"], ["npm test", "pytest"],
         "Run the tests with `{v}`, nothing else runs the integration ones.", "Old docs still say `{d}`, that's outdated."),
        ("Which Python version does the service run?", ["3.12", "3.11"], ["3.10", "3.13"],
         "The service is pinned to Python {v}.", "A contractor's laptop had Python {d} and things broke."),
        ("What is our default branch called?", ["main", "trunk"], ["master", "develop"],
         "Our default branch is `{v}`.", "The fork we imported still uses `{d}`."),
        ("Which timezone do we store timestamps in?", ["UTC"], ["local time", "CET"],
         "All timestamps are stored in {v}, always.", "The legacy exporter wrote {d}, that bug is fixed."),
        ("What fixed the connection pool timeouts?", ["raising pool_max to 20", "switching to the session-mode pooler"], ["restarting the worker", "adding retries"],
         "The pool timeouts were fixed by {v}.", "{d} was tried first and did nothing."),
    ],
}

NOISE = {
    "companion": [
        ("How was your day?", "Pretty good, thanks for asking. Anything on your mind?"),
        ("Recommend a film for tonight.", "If you liked slow burns, try a Scandinavian thriller."),
        ("I'm tired.", "Sounds like a long one. A short walk might help before dinner."),
        ("What's a good stretch after running?", "Calf raises on a step and a gentle hamstring stretch."),
        ("Remind me to call the plumber.", "Noted — I'll bring it up tomorrow morning."),
        ("Weather looks grim.", "Grey skies all week, according to the forecast."),
        ("Tell me a fun fact.", "Octopuses have three hearts and blue blood."),
        ("I finished the book.", "How was the ending? Worth the slow middle?"),
    ],
    "support": [
        ("Hi, is the office open on Sunday?", "Yes, 9 to 17 on Sundays."),
        ("Can I extend by one day?", "Of course, I've noted a possible extension; confirm on return."),
        ("The GPS was slow last time.", "Sorry about that, I've flagged the unit for a check."),
        ("Do you have winter tyres?", "Yes, fitted from November on all classes."),
        ("How early should I be at the desk?", "Fifteen minutes before pickup is enough."),
        ("Thanks, that's all.", "You're welcome, safe travels."),
        ("Is there a fee for a second driver?", "A small daily fee, waived for loyalty members."),
        ("Can I pay on return instead?", "Yes, payment on return is fine."),
    ],
    "coding": [
        ("CI is red again.", "Looking — flaky integration test, re-running."),
        ("Can you review the PR?", "On it; two nits, otherwise fine."),
        ("Bump the dependency?", "Bumped; changelog looks harmless."),
        ("Where are the logs?", "Same place as before, the service dashboard."),
        ("Lunch?", "After the deploy finishes."),
        ("Format this file.", "Done, formatter applied."),
        ("What's the ETA on the migration?", "About twenty minutes end to end."),
        ("Rename the variable.", "Renamed across the module."),
    ],
}

USER_NAME = {"companion": "Sam", "support": "the customer", "coding": "the team"}


def generate_corpus(out: Path, seed: int) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for ctype in TYPES:
        for scale, days in SCALES.items():
            rng = random.Random(f"{seed}:{ctype}:{scale}")
            slots = list(SLOTS[ctype])
            rng.shuffle(slots)
            n_cases = min(CASES_PER_SCALE[scale], len(slots))
            cases = []
            plants: dict[int, list[tuple[str, str]]] = {}  # day -> [(role, text)]
            for i, (question, trues, distractors, say_true, say_dist) in enumerate(slots[:n_cases]):
                true = rng.choice(trues)
                dist = rng.choice([d for d in distractors if d != true])
                # truth early (first 40% of days), distractor late (last 40%)
                d_true = rng.randint(1, max(1, int(days * 0.4)))
                d_dist = rng.randint(max(d_true + 1, int(days * 0.6)), days)
                plants.setdefault(d_true, []).append(("user", say_true.format(v=true)))
                plants.setdefault(d_dist, []).append(("user", say_dist.format(d=dist)))
                cases.append({"id": f"{ctype}-{scale}-{i+1:02d}", "question": question,
                              "correct": true, "distractor": dist,
                              "planted_day": d_true, "distractor_day": d_dist})
            turns = []
            for day in range(1, days + 1):
                n = rng.randint(*TURNS_PER_DAY)
                day_turns = []
                for _ in range(n // 2):
                    u, a = rng.choice(NOISE[ctype])
                    day_turns.append(("user", u))
                    day_turns.append(("assistant", a))
                for role, text in plants.get(day, []):
                    pos = rng.randint(0, len(day_turns))
                    day_turns.insert(pos, (role, text))
                    day_turns.insert(pos + 1, ("assistant", "Got it, noted."))
                for role, text in day_turns:
                    turns.append({"t": day, "role": role, "text": text})
            name = f"{ctype}_{scale}"
            with open(out / f"{name}.jsonl", "w") as f:
                for t in turns:
                    f.write(json.dumps(t, ensure_ascii=False) + "\n")
            with open(out / f"{name}.truth.json", "w") as f:
                json.dump({"type": ctype, "scale": scale, "days": days, "seed": seed,
                           "cases": cases}, f, ensure_ascii=False, indent=1)
            print(f"  {name:14} {days:>3} days  {len(turns):>4} turns  {len(cases):>2} cases")


def load_corpus(corpus: Path, ctype: str, scale: str):
    name = f"{ctype}_{scale}"
    turns = [json.loads(l) for l in open(corpus / f"{name}.jsonl") if l.strip()]
    truth = json.load(open(corpus / f"{name}.truth.json"))
    return turns, truth


def by_day(turns):
    days: dict[int, list] = {}
    for t in turns:
        days.setdefault(t["t"], []).append(t)
    return [days[d] for d in sorted(days)]


def render(turns) -> str:
    return "\n".join(f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['text']}" for t in turns)


# ---------------------------------------------------------------------------
# The answer model (shared by every system) — OpenAI-compatible chat, T=0
# ---------------------------------------------------------------------------

def llm(messages, max_tokens=300, json_mode=False) -> str:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is required to answer questions (and for the sandbox baseline).")
    body = {"model": ANSWER_MODEL, "messages": messages, "temperature": 0, "max_tokens": max_tokens}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(f"{OPENAI_URL}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as r:
                return json.load(r)["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))


_ENC = None

def count_tokens(text: str) -> int:
    """Exact token count for the answer model's tokenizer (o200k_base: gpt-4o
    family) when tiktoken is installed; the chars/4 estimate otherwise. E2
    reports both so the cost ratio does not rest on an estimate."""
    global _ENC
    if _ENC is None:
        try:
            import tiktoken
            _ENC = tiktoken.get_encoding("o200k_base")
        except Exception:
            _ENC = False
    if not _ENC:
        return estimate_tokens(text)
    return len(_ENC.encode(text or ""))


def answer(question: str, context: str) -> str:
    # Without a model key the run still measures retrieval (did the correct
    # value reach the context?) and tokens; the answer column reads "unscored".
    if os.environ.get("BENCH_SKIP_ANSWER") or not os.environ.get("OPENAI_API_KEY"):
        return "unscored"
    return llm([
        {"role": "system", "content": "You answer questions about a person or project strictly from the context "
                                      "provided. Reply with the value only, a few words, no explanation. If the "
                                      "context does not contain the answer, reply exactly: unknown."},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"},
    ], max_tokens=40).strip()


_STOP = {"the", "a", "an", "to", "in", "at", "of", "my", "our", "on", "sent", "is", "it", "and", "with"}


def _keys(value: str) -> list[str]:
    """The words that carry a value: lowercase, articles and glue dropped, so
    "the Visa ending 4471" and "Visa ending in 4471." are the same answer and
    "the airport desk" matches "airport desk". Exact-substring matching scored
    the full-history baseline at 0.38 on support in r1 — a ruler defect, not a
    memory one (see QUEUE.md)."""
    toks = re.findall(r"[a-z0-9][a-z0-9\-']*", value.lower())
    return [t for t in toks if t not in _STOP] or toks


def _matches(answer: str, value: str) -> bool:
    a = answer.lower()
    return all(k in a for k in _keys(value))


def score_answer(ans: str, correct: str, distractor: str) -> str:
    if _matches(ans, correct):
        return "correct"
    if _matches(ans, distractor):
        return "distractor"
    return "other"


# ---------------------------------------------------------------------------
# System: full history
# ---------------------------------------------------------------------------

def run_full(turns, truth, opts):
    context = render(turns)
    rows = []
    for c in truth["cases"]:
        ans = answer(c["question"], context)
        rows.append({**c, "answer": ans, "verdict": score_answer(ans, c["correct"], c["distractor"]),
                     "tokens": estimate_tokens(context), "tokens_exact": count_tokens(context),
                     "recalled": _matches(context, c["correct"])})
    return rows, {"stored_facts": None, "junk": None}


# ---------------------------------------------------------------------------
# System: OpenAI sandbox memory, reproduced from the open source
# ---------------------------------------------------------------------------

PHASE1 = """You are extracting memory from one agent session ("rollout").
Return JSON: {"noop": true} when nothing durable happened — no-op is allowed and preferred.
Otherwise return {"rollout_summary": "<3-6 sentences on what happened>",
"rollout_slug": "<3-word-slug>", "raw_memory": "<compact notes: durable facts about the user
or project, preferences, decisions, failures; evidence from the user's own messages weighs
more than the assistant's; never include secrets>"}."""

PHASE2 = """You maintain two files from a set of raw memory notes.
Return JSON with two keys.
"MEMORY_md": a rewrite of MEMORY.md with exactly these sections:
# Task Group
## Task n  (one per distinct task/topic, with ### rollout_summary_files listing the note ids and ### keywords)
## User preferences
## Reusable knowledge
## Failures
Keep only what the notes support; when notes disagree, keep the newest and say so.
"memory_summary_md": memory_summary.md — "User Profile" (<= 500 words), "User preferences",
"General Tips", and a "What's in Memory" index of the task groups with their keywords."""


def run_sandbox(turns, truth, opts):
    work = opts["workdir"] / "sandbox"
    (work / "raw_memories").mkdir(parents=True, exist_ok=True)
    raw: list[tuple[int, str]] = []
    for i, day in enumerate(by_day(turns), 1):
        out = llm([{"role": "system", "content": PHASE1},
                   {"role": "user", "content": render(day)}], max_tokens=500, json_mode=True)
        try:
            d = json.loads(out)
        except Exception:
            d = {"noop": True}
        if d.get("noop") or not d.get("raw_memory"):
            continue
        note = f"# rollout {i} ({d.get('rollout_slug', '')})\n{d.get('rollout_summary', '')}\n\n{d['raw_memory']}\n"
        (work / "raw_memories" / f"{i:03d}.md").write_text(note)
        raw.append((i, note))
    # consolidation: keep the newest MAX_RAW (the SDK's 256) — never reached at <= 90 days
    raw = raw[-256:]
    notes = "\n\n".join(n for _, n in raw) or "(no notes)"
    out = llm([{"role": "system", "content": PHASE2},
               {"role": "user", "content": notes}], max_tokens=2500, json_mode=True)
    try:
        d = json.loads(out)
    except Exception:
        d = {"MEMORY_md": notes, "memory_summary_md": notes[:4000]}
    memory_md = d.get("MEMORY_md", "")
    summary = d.get("memory_summary_md", "")
    (work / "MEMORY.md").write_text(memory_md)
    (work / "memory_summary.md").write_text(summary)
    # Codex caps the injected summary at 2,500 tokens
    summary_inj = truncate_tokens(summary, 2500)
    haystack = [memory_md] + [n for _, n in raw]
    rows = []
    for c in truth["cases"]:
        kw = llm([{"role": "system", "content": "Given a question and a memory index, list up to 6 short "
                                                "keywords to grep the memory files with. JSON: {\"keywords\": [...]}"},
                  {"role": "user", "content": f"Index:\n{summary_inj}\n\nQuestion: {c['question']}"}],
                 max_tokens=80, json_mode=True)
        try:
            keywords = [k for k in json.loads(kw).get("keywords", []) if isinstance(k, str)][:6]
        except Exception:
            keywords = []
        hits = []
        for text in haystack:
            for line in text.splitlines():
                if any(k.lower() in line.lower() for k in keywords):
                    hits.append(line)
        grep = truncate_tokens("\n".join(dict.fromkeys(hits)), 1500)
        context = f"{summary_inj}\n\n--- grep results ---\n{grep}"
        ans = answer(c["question"], context)
        rows.append({**c, "answer": ans, "verdict": score_answer(ans, c["correct"], c["distractor"]),
                     "tokens": estimate_tokens(context), "recalled": _matches(context, c["correct"]),
                     "keywords": keywords, "context": context[:1200]})
    stored = [ln.lstrip("-*0123456789. ").strip() for ln in memory_md.splitlines()
              if ln.strip() and not ln.strip().startswith("#")]
    return rows, junk_report(stored, truth)


def truncate_tokens(text: str, max_tokens: int) -> str:
    if estimate_tokens(text) <= max_tokens:
        return text
    return text[: max_tokens * 4]


# ---------------------------------------------------------------------------
# System: Mengram cloud, throwaway sub-user per run
# ---------------------------------------------------------------------------

def _mengram_key():
    k = os.environ.get("MENGRAM_API_KEY")
    if k:
        return k
    return json.load(open(Path.home() / ".mengram" / "config.json"))["api_key"]


def run_mengram(turns, truth, opts):
    from cloud.client import CloudMemory
    mem = CloudMemory(api_key=_mengram_key(), base_url=os.environ.get("MENGRAM_BASE_URL") or None)
    sub = f"bench-{opts['run']}-{truth['type']}-{truth['scale']}"
    jobs = []
    failed = 0
    # One day at a time, as a real app would send them: the next day's add
    # must see what the previous one wrote (dedup, context, the gate). Firing
    # all days at once also exhausted the API's connection pool locally.
    for day in by_day(turns):
        msgs = [{"role": t["role"], "content": t["text"]} for t in day]
        r = mem.add(msgs, user_id=sub)
        j = r.get("job_id")
        if not j:
            continue
        jobs.append(j)
        t_job = time.time()
        while time.time() - t_job < 300:
            try:
                st = mem._request("GET", f"/v1/jobs/{j}")
            except Exception:
                failed += 1
                break
            if st.get("status") in ("done", "completed"):
                break
            if st.get("status") in ("failed", "error"):
                failed += 1
                break
            time.sleep(1)
    # wait for anything still running
    deadline = time.time() + 900
    pending = set(jobs[-1:])
    while pending and time.time() < deadline:
        for j in list(pending):
            try:
                st = mem._request("GET", f"/v1/jobs/{j}")
                if st.get("status") in ("done", "completed", "failed", "error"):
                    pending.discard(j)
                    if st.get("status") in ("failed", "error"):
                        failed += 1
            except Exception:
                pending.discard(j)
                failed += 1
        time.sleep(3)
    if failed or pending:
        print(f"WARNING: {failed} add job(s) failed, {len(pending)} still pending — the run is not valid",
              file=sys.stderr)
    rows = []
    for c in truth["cases"]:
        res = mem.search_all(c["question"], limit=5, user_id=sub, max_tokens=opts["max_tokens"])
        context = render_search_all(res)
        ans = answer(c["question"], context)
        rows.append({**c, "answer": ans, "verdict": score_answer(ans, c["correct"], c["distractor"]),
                     "tokens": (res.get("budget") or {}).get("used_tokens") or estimate_tokens(context),
                     "tokens_exact": count_tokens(context),
                     "recalled": _matches(context, c["correct"]), "context": context[:1200]})
    stored = []
    try:
        ents = mem.get_all_full(user_id=sub)
        for e in ents:
            stored += [f for f in (e.get("facts") or [])]
    except Exception:
        pass
    return rows, {**junk_report(stored, truth), "jobs": len(jobs), "jobs_failed": failed + len(pending)}


def render_search_all(res) -> str:
    lines = []
    for r in res.get("semantic") or []:
        lines.append(f"{r.get('entity')}:")
        lines += [f"  - {f}" for f in (r.get("facts") or [])]
    for ep in res.get("episodic") or []:
        lines.append(f"- event: {ep.get('summary', '')}")
    for p in res.get("procedural") or []:
        lines.append(f"- procedure: {p.get('name', '')}")
    for ch in res.get("chunks") or []:
        lines.append(f"- {ch.get('content') or ch.get('text') or ''}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Junk: what was stored that no question ever needs
# ---------------------------------------------------------------------------

def junk_report(stored: list[str], truth) -> dict:
    if not stored:
        return {"stored_facts": 0, "needed": 0, "distractor_stored": 0, "junk": 0, "junk_rate": None}
    needed_vals = [c["correct"].lower() for c in truth["cases"]]
    dist_vals = [c["distractor"].lower() for c in truth["cases"]]
    needed = sum(1 for f in stored if any(_matches(f, v) for v in needed_vals))
    dist = sum(1 for f in stored if any(_matches(f, v) for v in dist_vals)
               and not any(_matches(f, v) for v in needed_vals))
    junk = len(stored) - needed - dist
    return {"stored_facts": len(stored), "needed": needed, "distractor_stored": dist,
            "junk": junk, "junk_rate": round(junk / len(stored), 3)}


# ---------------------------------------------------------------------------
# run / report
# ---------------------------------------------------------------------------

SYSTEMS = {"full": run_full, "sandbox": run_sandbox, "mengram": run_mengram}


def cmd_run(a):
    corpus = Path(a.corpus)
    results = Path(a.results); results.mkdir(parents=True, exist_ok=True)
    turns, truth = load_corpus(corpus, a.type, a.scale)
    workdir = results / f"{a.run}-{a.system}-{a.type}-{a.scale}"
    workdir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    rows, junk = SYSTEMS[a.system](turns, truth, {"run": a.run, "workdir": workdir, "max_tokens": a.max_tokens})
    n = len(rows)
    summary = {
        "run": a.run, "system": a.system, "type": a.type, "scale": a.scale, "days": truth["days"],
        "model": ANSWER_MODEL, "cases": n,
        "recall_at_old": round(sum(r["verdict"] == "correct" for r in rows) / n, 3),
        "distractor_rate": round(sum(r["verdict"] == "distractor" for r in rows) / n, 3),
        "tokens_exact_per_question": round(sum(r.get("tokens_exact") or 0 for r in rows) / n),
        "retrieved_rate": round(sum(bool(r["recalled"]) for r in rows) / n, 3),
        "tokens_per_question": round(sum(r["tokens"] for r in rows) / n),
        **junk, "seconds": round(time.time() - t0),
    }
    json.dump({"summary": summary, "rows": rows}, open(workdir / "result.json", "w"), indent=1, ensure_ascii=False)
    print(json.dumps(summary))


def cmd_rescore(a):
    """Recompute verdicts from stored answers with the current matcher; no model calls."""
    for p in Path(a.dir).glob("*/result.json"):
        d = json.load(open(p))
        rows = d["rows"]
        if not rows or rows[0].get("answer") == "unscored":
            continue
        for r in rows:
            r["verdict"] = score_answer(r["answer"], r["correct"], r["distractor"])
            if "context" in r:
                r["recalled"] = _matches(r["context"], r["correct"])
        n = len(rows)
        d["summary"]["recall_at_old"] = round(sum(r["verdict"] == "correct" for r in rows) / n, 3)
        d["summary"]["distractor_rate"] = round(sum(r["verdict"] == "distractor" for r in rows) / n, 3)
        d["summary"]["retrieved_rate"] = round(sum(bool(r["recalled"]) for r in rows) / n, 3)
        json.dump(d, open(p, "w"), indent=1, ensure_ascii=False)
    print("rescored")


def cmd_report(a):
    rows = []
    for p in Path(a.dir).glob("*/result.json"):
        d = json.load(open(p))
        if d["rows"] and d["rows"][0].get("answer") == "unscored":
            continue   # a retrieval-only smoke run is not a scored run
        rows.append(d["summary"])
    rows.sort(key=lambda r: (r["type"], r["scale"], r["system"], r["run"]))
    print(f"{'type':10} {'sc':2} {'system':8} {'run':6} {'recall@old':>10} {'distr':>6} {'retr':>6} {'tok/q':>7} {'junk':>6}")
    for r in rows:
        junk = "-" if r.get("junk_rate") is None else f"{r['junk_rate']:.2f}"
        print(f"{r['type']:10} {r['scale']:2} {r['system']:8} {r['run']:6} {r['recall_at_old']:>10.2f} "
              f"{r['distractor_rate']:>6.2f} {r['retrieved_rate']:>6.2f} {r['tokens_per_question']:>7} {junk:>6}")
    # variance check for the E0 criterion: same system/type/scale, different runs
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r["type"], r["scale"], r["system"]), []).append(r["recall_at_old"])
    worst = 0.0
    for k, v in groups.items():
        if len(v) >= 2:
            worst = max(worst, max(v) - min(v))
    if any(len(v) >= 2 for v in groups.values()):
        print(f"\nlargest recall@old spread between runs of the same system/corpus: {worst:.2f} "
              f"({'within' if worst < 0.05 else 'OUTSIDE'} the pre-registered 5%)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate"); g.add_argument("--out", default=str(HERE / "corpus")); g.add_argument("--seed", type=int, default=20260916)
    r = sub.add_parser("run")
    r.add_argument("--corpus", default=str(HERE / "corpus")); r.add_argument("--results", default=str(HERE / "results"))
    r.add_argument("--system", choices=list(SYSTEMS), required=True)
    r.add_argument("--type", choices=TYPES, required=True); r.add_argument("--scale", choices=list(SCALES), required=True)
    r.add_argument("--run", default="r1"); r.add_argument("--max-tokens", type=int, default=600, dest="max_tokens")
    rp = sub.add_parser("report"); rp.add_argument("--dir", default=str(HERE / "results"))
    rs = sub.add_parser("rescore"); rs.add_argument("--dir", default=str(HERE / "results"))
    a = p.parse_args()
    if a.cmd == "generate":
        generate_corpus(Path(a.out), a.seed)
    elif a.cmd == "run":
        cmd_run(a)
    elif a.cmd == "rescore":
        cmd_rescore(a)
    else:
        cmd_report(a)


if __name__ == "__main__":
    main()
